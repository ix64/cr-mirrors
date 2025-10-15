import dataclasses
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape


@dataclasses.dataclass
class RegistryUpstream:
    name: str
    label: str

    prefix: Optional[str] = None
    prefixes: Optional[List[str]] = None

    gallery: Optional[str] = None
    endpoint: Optional[str] = None
    example_image: Optional[str] = None

    def get_prefixes(self):
        if self.prefixes is not None:
            return self.prefixes
        elif self.prefix is not None:
            return [self.prefix]
        else:
            logging.fatal(f"no prefixes for {self.name}")
            return []

    def get_endpoint(self):
        if self.endpoint is not None:
            return self.endpoint
        elif self.prefix is not None:
            return f"https://{self.prefix}"
        else:
            logging.fatal(f"no endpoint for {self.name}")


def load_known_registries(path: Path | str) -> List[RegistryUpstream]:
    with open(path, "r", encoding="utf-8") as _f:
        known_registries = json.load(_f)

    return [RegistryUpstream(**x) for x in known_registries]


class IndexGenerator:
    domain_usages: List[Tuple[str, str, str]] = []
    prefix_usages: List[Tuple[str, str, str]] = []
    docker_domain: Optional[str] = None

    _docker_prefix_extra = [
        ("nginx:latest", "%s/docker.io/library/nginx:latest"),
        ("grafana/grafana:latest", "%s/docker.io/grafana/grafana:latest"),
    ]
    _docker_domain_extra = [
        ("nginx:latest", "%s/library/nginx:latest"),
        ("grafana/grafana:latest", "%s/grafana/grafana:latest"),
    ]

    def __init__(self, gateway: str):
        self.gateway = gateway

    def add_prefix_usage(self, upstream: RegistryUpstream):
        example_image = "foo/bar:latest" if upstream.example_image is None else upstream.example_image

        if upstream.name == "docker":
            for (src, dst) in self._docker_prefix_extra:
                self.prefix_usages.append((
                    upstream.label,
                    src,
                    dst % self.gateway,
                ))

        for prefix in upstream.get_prefixes():
            self.prefix_usages.append((
                upstream.label,
                f"{prefix}/{example_image}",
                f"{self.gateway}/{prefix}/{example_image}",
            ))

    def add_domain_usage(self, domain: str, upstream: RegistryUpstream):
        example_image = "foo/bar:latest" if upstream.example_image is None else upstream.example_image

        if upstream.name == "docker":
            self.docker_domain = domain
            for (src, dst) in self._docker_domain_extra:
                self.domain_usages.append((
                    upstream.label,
                    src,
                    dst % domain,
                ))

        for prefix in upstream.get_prefixes():
            self.domain_usages.append((
                upstream.label,
                f"{prefix}/{example_image}",
                f"{domain}/{example_image}",
            ))

    def generate(self, title):
        env = Environment(
            loader=FileSystemLoader("templates"),
            autoescape=select_autoescape()
        )

        template = env.get_template("index.html")
        ret = template.render(
            site_title=title,
            docker_domain=self.docker_domain,
            prefix_usages=self.prefix_usages,
            domain_usages=self.domain_usages,
        )
        return ret


class ComposeGenerator:
    # env for registry service, eg. proxy config
    extra_env = {}

    # add gateway to the beginning of image name
    # e.g. docker.io/library/alpine -> m.example.com/docker.io/library/alpine
    prefix_mode = True

    # replace image prefix with custom domain (auto generated if not specified)
    # e.g. docker.io/library/alpine -> docker.m.example.com/library/alpine
    domain_mode = True

    site_title = "Container Registry Mirrors"

    http_port: int = 80
    https_port: int | None = None
    traefik_dashboard_domain: str | None = None
    traefik_dashboard_port: int | None = None

    redirect_explict_https: bool = False

    project_name: str = "mcr"
    traefik_image: str = "docker.io/library/traefik:3.0"
    registry_image: str = "docker.io/library/registry:2.8"
    nginx_image: str = "docker.io/library/nginx:1.27"

    trust_proxies: List[str] = []

    gateway: str

    _known_registries: Dict[str, RegistryUpstream] = []

    _route_index = 0
    _compose = {"services": {}}

    _extra_files: Dict[str, str] = {}

    def __init__(self, gateway: str) -> None:
        self.gateway = gateway
        self._usage_generator = IndexGenerator(gateway)

        known_registries_path = Path("./known_registries.json")
        if known_registries_path.exists():
            registries = load_known_registries(known_registries_path)
            self._known_registries = {x.name: x for x in registries}

    def generate(self, root_dir: Path | str):
        self._generate_index()
        self._setup_gateway()

        self._compose["name"] = self.project_name

        root_dir = Path(root_dir)

        for fn, val in self._extra_files.items():
            fp = root_dir / fn
            fp.parent.mkdir(parents=True, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(val)

        with open(root_dir / "compose.yaml", "w", encoding="utf-8") as f:
            yaml.dump(self._compose, f)

    def _setup_gateway(self):

        svc = {
            "restart": "unless-stopped",
            "image": self.traefik_image,
            "ports": [f"{self.http_port}:{self.http_port}"],
            "labels": [],
            "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
            "command": [
                "--log.level=DEBUG",
                "--accessLog=True",
                "--providers.docker.exposedByDefault=false",
                f"--entryPoints.http.address=:{self.http_port}",
                "--entryPoints.http.asDefault=true",
            ],
        }

        if len(self.trust_proxies) != 0:
            svc["command"] += ["--entryPoints.http.forwardedHeaders.trustedIPs=" + "".join(self.trust_proxies)]

        if self.https_port is not None:
            svc["ports"] += [f"{self.https_port}:{self.https_port}"]
            svc["command"] += [
                f"--entryPoints.https.address=:{self.https_port}"
                "--entryPoints.https.asDefault=true",
            ]
            if len(self.trust_proxies) != 0:
                svc["command"] += ["--entryPoints.http.forwardedHeaders.trustedIPs=" + "".join(self.trust_proxies)]

            raise NotImplemented("TODO: Setup TLS Cert")

        if self.traefik_dashboard_port is not None:
            svc["ports"] += [
                f"{self.traefik_dashboard_port}:{self.traefik_dashboard_port}"
            ]

        if (
                self.traefik_dashboard_domain is not None
                or self.traefik_dashboard_port is not None
        ):
            svc["command"] += ["--api.dashboard=true"]
            svc["labels"] += [
                "traefik.enable=true",
                "traefik.http.services.dummyService.loadBalancer.server.port=1337",
            ]

        if self.traefik_dashboard_port is not None:
            name = "traefik-dashboard-port"
            svc["command"] += [
                f"--entryPoints.traefik-dashboard.address=:{self.traefik_dashboard_port}"
            ]
            svc["labels"] += [
                f"traefik.http.routers.{name}.service=api@internal",
                f"traefik.http.routers.{name}.rule=PathPrefix(`/api`) || PathPrefix(`/dashboard`)",
                f"traefik.http.routers.{name}.entrypoints=traefik-dashboard",
            ]

        if self.traefik_dashboard_domain is not None:
            name = "traefik-dashboard-domain"
            svc["labels"] += [
                f"traefik.http.routers.{name}.service=api@internal",
                f"traefik.http.routers.{name}.rule=" +
                f"Host(`{self.traefik_dashboard_domain}`) && (PathPrefix(`/api`) || PathPrefix(`/dashboard`))",
            ]

        self._compose["services"]["gateway"] = svc

    def _generate_index(self):
        try:
            index_html = self._usage_generator.generate(self.site_title)
            self._extra_files["static/index.html"] = index_html
        except Exception as e:
            logging.warning("render index page failed: %s", e)
            return

        service_name = "nginx-static"
        service_config = {
            "image": self.nginx_image,
            "restart": "unless-stopped",
            "volumes": [
                f"./static:/usr/share/nginx/html:ro",
            ],
            "labels": [
                "traefik.enable=true",
                f"traefik.http.services.{service_name}.loadBalancer.server.port=80",
                f"traefik.http.routers.{service_name}.rule=Host(`{self.gateway}`)",
                f"traefik.http.routers.{service_name}.service={service_name}",
            ],
        }
        self._compose["services"][service_name] = service_config

    def add_known_registry(
            self,
            name: str,
            upstream_name: str,
            domains: Optional[List[str]] = None,
    ):
        if upstream_name in self._known_registries:
            upstream = self._known_registries[name]
        else:
            raise ValueError(f"Unknown upstream {upstream_name}")

        self._add_mapping(name, upstream, domains)

    def add_custom_registry(self, name: str, upstream: RegistryUpstream, domains: Optional[List[str]] = None):
        self._add_mapping(name, upstream, domains)

    def add_known_registry_bulk(self, name_list: List[str]):
        for name in name_list:
            self.add_known_registry(name, name)

    def _add_mapping(self, name: str, upstream: RegistryUpstream, domains: Optional[List[str]] = None):

        svc_name, svc_conf, svc_endpoint = self._configure_cache_service(
            name, upstream
        )

        if self.prefix_mode:
            self._usage_generator.add_prefix_usage(upstream)
            for prefix in upstream.get_prefixes():
                svc_conf = self._configure_prefix_route(svc_name, svc_conf, prefix)

        if self.domain_mode:
            if domains is None:
                domains = [f"{name}.{self.gateway}"]

            for d in domains:
                svc_conf = self._configure_domain_route(svc_name, svc_conf, d)
                self._usage_generator.add_domain_usage(d, upstream)

        self._compose["services"][svc_name] = svc_conf

    def _configure_cache_service(self, name: str, upstream: RegistryUpstream):

        port = 5000
        hostname = f"{name}.registry.internal"

        service_name = f"registry-{name}"
        service_endpoint = f"{hostname}:{port}"

        env = self.extra_env.copy()
        env["REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY"] = "/var/lib/registry"
        env["REGISTRY_HTTP_ADDR"] = f":{port}"
        env["REGISTRY_PROXY_REMOTEURL"] = upstream.get_endpoint()

        # TODO: support override registry config
        service_config = {
            "image": self.registry_image,
            "restart": "unless-stopped",
            "hostname": hostname,
            "environment": env,
            "volumes": [
                f"./cache/{name}:/var/lib/registry:rw",
            ],
            "labels": [
                "traefik.enable=true",
                f"traefik.http.services.{service_name}.loadBalancer.server.port={port}",
            ],
        }
        return service_name, service_config, service_endpoint

    def _configure_prefix_route(self, svc_name, svc, prefix: str):
        route_name = f"{svc_name}-{self._route_index}"
        labels = [
            f"traefik.http.middlewares.{route_name}-strip.stripPrefix.prefixes=/v2/{prefix}",
            f"traefik.http.middlewares.{route_name}-add.addPrefix.prefix=/v2",
            f"traefik.http.routers.{route_name}.rule=" +
            f"Host(`{self.gateway}`) && PathPrefix(`/v2/{prefix}/`)",
            f"traefik.http.routers.{route_name}.service={svc_name}",
            f"traefik.http.routers.{route_name}.middlewares={route_name}-strip,{route_name}-add",
        ]
        self._route_index += 1

        svc["labels"] += labels
        return svc

    def _configure_domain_route(self, svc_name, svc, domain: str):
        route_name = f"{svc_name}-{self._route_index}"
        labels = [
            f"traefik.http.middlewares.{route_name}-redir-home.redirectregex.regex=^(\w+)://{domain}/$",
            (
                f"traefik.http.middlewares.{route_name}-redir-home.redirectregex.replacement=https://{self.gateway}/"
                if self.redirect_explict_https
                else f"traefik.http.middlewares.{route_name}-redir-home.redirectregex.replacement=$${1}://{self.gateway}/"
            ),
            f"traefik.http.routers.{route_name}.rule=Host(`{domain}`)",
            f"traefik.http.routers.{route_name}.service={svc_name}",
            f"traefik.http.routers.{route_name}.middlewares={route_name}-redir-home",
        ]
        self._route_index += 1

        svc["labels"] += labels
        return svc
