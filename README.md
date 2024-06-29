# Container Registry Mirrors

Pull Through Cache Mirroring for Multiple Container Registries.

## Highlights

- **Multiple Upstream**: Easily configure and manage cache mirrors for various container registries, including
    - Docker Hub
    - GitHub Package Registry
    - Red Hat Quay
    - Kubernetes Container Registry
    - ...
- **Domain Replacement Mode**
  ```
  Before:            docker.io/library/hello-world
  After:  docker.m.example.com/library/hello-world
  ```
- **Prefix Addition Mode**
  ```
  Before:               docker.io/library/hello-world
  After:  m.example.com/docker.io/library/hello-world
  ```
- **Flexible Configuration**

## In Progress

- Automatic TLS

## Get Started

### Setup DNS

- Take `m.example.com` as gateway, `192.0.2.1` as server IP
- If `Prefix Addition Mode` enabled
    ```
    m.example.com.      IN    A    192.0.2.1
    ```
- If `Domain Replacement Mode` enabled
    ```
    *.m.example.com.    IN    A    192.0.2.1
    ```

### Setup dependencies

1. Python3
    - PyYAML
    - Jinja2
2. Docker
    - Docker Compose

```bash
# Example for Debian 12
sudo apt install curl python3 python3-yaml python3-jinja2
curl -fsSL https://get.docker.com | sudo sh
```

### Generate & Run

```bash
# Copy `generate.example.py` to `generate.py`
cp generate.example.py generate.py

# Edit `generate.py`
editor generate.py

# Generate `compose.yaml`
python3 ./generate.py

# Boot
docker compose up -d

# Inspect logs 
docker compose logs -f
```