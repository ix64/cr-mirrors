#!/usr/bin/env python3

from base import ComposeGenerator

if __name__ == "__main__":
    # TODO: this is an example, modify before use
    g = ComposeGenerator("m.example.com")

    g.extra_env = {
        "https_proxy": "socks5://outbound.example.com:8080",
        "http_proxy": "socks5://outbound.example.com:8080",
        "no_proxy": ",".join([
            "localhost",
            "127.0.0.0/8",
            "10.0.0.0/8",
            "100.64.0.0/10",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "*.example.com",
        ]),
    }

    g.http_port = 8080
    g.traefik_dashboard_port = 8081
    g.redirect_explict_https = True

    # configure registry & generate docker compose file
    g.add_known_registry_bulk(["docker", "ghcr", "quay", "k8s"])
    g.generate(".")
