import dataclasses
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclasses.dataclass
class RegistryUpstream:
    name: str
    label: str

    prefix: Optional[str] = None
    prefixes: Optional[List[str]] = None

    gallery: Optional[str] = None
    endpoint: Optional[str] = None

    def get_prefixes(self):
        if self.prefixes is not None:
            return self.prefixes
        elif self.prefix is not None:
            return [self.prefix]
        else:
            logging.warning(f"no prefixes for {self.name}")
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


class ComposeGenerator:
    # env for registry service, eg. proxy config
    extra_env = {}

    # add gateway to the beginning of image name
    # e.g. docker.io/library/alpine -> m.example.com/docker.io/library/alpine
    prefix_mode = True

    # replace image prefix with custom domain (auto generated if not specified)
    # e.g. docker.io/library/alpine -> docker.m.example.com/library/alpine
    domain_mode = True

    http_port: int = 80
    https_port: int | None = None
    traefik_dashboard_domain: str | None = None
    traefik_dashboard_port: int | None = None

    project_name: str = "mcr"
    traefik_image: str = "docker.io/library/traefik:3.0"
    registry_image: str = "docker.io/library/registry:2.8"

    trust_proxies: List[str] = []

    _route_index = 0

    _compose = {"services": {}}
    _extra_files: Dict[str, str] = {}

    gateway: str
    _known_registries: Dict[str, RegistryUpstream] = []

    def __init__(self, gateway: str) -> None:
        self.gateway = gateway

        known_registries_path = Path("./known_registries.json")
        if known_registries_path.exists():
            registries = load_known_registries(known_registries_path)
            self._known_registries = {x.name: x for x in registries}

    def generate(self, root_dir: Path | str):
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
                f"traefik.http.routers.{name}.entrypoints=http",
            ]

        self._compose["services"]["gateway"] = svc

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

    def add_custom_registry(self, name: str, upstream: RegistryUpstream, domains: Optional[List[str]] = None, ):
        self._add_mapping(name, upstream, domains)

    def add_known_registry_bulk(self, name_list: List[str]):
        for name in name_list:
            self.add_known_registry(name, name)

    def _add_mapping(self, name: str, upstream: RegistryUpstream, domains: Optional[List[str]] = None, ):

        svc_name, svc_conf, svc_endpoint = self._configure_cache_service(
            name, upstream
        )

        if self.prefix_mode:

            for prefix in upstream.get_prefixes():
                svc_conf = self._configure_prefix_route(svc_name, svc_conf, prefix)

        if self.domain_mode:
            if domains is None:
                svc_conf = self._configure_domain_route(
                    svc_name, svc_conf, f"{name}.{self.gateway}"
                )
            else:
                for d in domains:
                    svc_conf = self._configure_domain_route(svc_name, svc_conf, d)

        self._compose["services"][svc_name] = svc_conf

    def _configure_cache_service(self, name: str, upstream: RegistryUpstream):

        port = 5000
        hostname = f"{name}.registry.internal"

        service_name = f"registry-{name}"
        service_endpoint = f"{hostname}:{port}"

        env = self.extra_env.copy()
        env["REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY"] = "/var/lib/registry"
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

        labels = [
            f"traefik.http.routers.{svc_name}-{self._route_index}.rule=Host(`{domain}`)",
            f"traefik.http.routers.{svc_name}-{self._route_index}.service={svc_name}",
        ]
        self._route_index += 1

        svc["labels"] += labels
        return svc
