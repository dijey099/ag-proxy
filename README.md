
# AG Proxy

This project provides an Antigravity proxy that intercepts AG IDE requests and routes AI Code assist requests to Openrouter. It consists of two main components:

- [`ag_patcher.py`](file:///home/sniper099/Music/AG_Proxy/ag_patcher.py): A script to patch the Antigravity IDE to route traffic through the local proxy.
- [`ag_proxy.py`](file:///home/sniper009/Music/AG_Proxy/ag_proxy.py): The proxy server that handles requests from the AG IDE and forwards them to Openrouter.

## Table of Contents

- [AG Proxy](#ag-proxy)
  - [Overview](#overview)
  - [Installation](#installation)
  - [Usage](#usage)
    - [Patcher Script (`ag_patcher.py`)](#patcher-script-ag_patcher.py)
    - [Proxy Server (`ag_proxy.py`)](#proxy-server-ag_proxy.py)
      - [Development (Python)](#development-python)
      - [Production (Gunicorn)](#production-gunicorn)
      - [Production (Docker)](#production-docker)
  - [Configuration](#configuration)

## Overview

The Antigravity Proxy allows you to redirect AI code assist requests from your Antigravity IDE to Openrouter, giving you more control over your AI model choices and potentially offering cost savings or access to different models.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/dijey099/ag-proxy.git
    cd ag-proxy
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Create a `.env` file:**
    Create a file named `.env` in the root directory of the project and add your Openrouter API key:
    ```
    OPENROUTER_API_KEY=sk-your-openrouter-api-key
    PROXY_ADDRESS=127.0.0.1
    PROXY_PORT=9099
    ```
    Make sure to replace `"sk-your-openrouter-api-key"` with your actual Openrouter API key.

## Usage

### Patcher Script (`ag_patcher.py`)

The `ag_patcher.py` script modifies your Antigravity IDE configuration to direct its AI code assist traffic to the local proxy server.

> [!NOTE]
> **AG IDE dictory path:** \
> **- WINDOWS:** *"C:\Users\\%USERNAME%\AppData\Local\Programs\Antigravity IDE"* (Don't forget double quote) \
> **- LINUX:** Depends on installation type (portable, package manager, source, ...) \

**Command-line Interface (CLI) Options:**

```bash
python ag_patcher.py --help
```

**Patching the IDE:**

To patch the IDE to use the proxy running on `localhost:9099` (default):

```bash
python ag_patcher.py --patch <AG IDE dictory path>
```

To specify a different host and port:

```bash
python ag_patcher.py --url http://192.168.1.100:9099 --patch <AG IDE dictory path>
```

**Unpatching the IDE:**

To revert the changes made by the patcher:

```bash
python ag_patcher.py --unpatch <AG IDE dictory path>
```

### Proxy Server (`ag_proxy.py`)

The `ag_proxy.py` script runs the proxy server that handles the requests.

#### Development (Python)

For development purposes, you can run the proxy server directly using Python:

```bash
python ag_proxy.py
```

By default, the server will run on `localhost:9099`. You can change the host and port by modifying the `PROXY_ADDRESS` and `PROXY_PORT` variables in `.env` or by setting them as environment variables.

#### Production (Gunicorn)

For production deployments, it is recommended to use a WSGI HTTP server like Gunicorn.

1.  **Install Gunicorn:**
    ```bash
    pip install gunicorn
    ```

2.  **Run with Gunicorn:**
    ```bash
    gunicorn -w 4 -t 5 -b 0.0.0.0:9099 ag_proxy:app
    ```

    -   `-w 4`: Runs 4 worker processes. Adjust based on your server's resources.
    -   `-t 5`: Runs 5 threads per worker. Adjust based on your server's resources.
    -   `-b 0.0.0.0:9099`: Binds the server to all network interfaces on port 9099.
    -   `ag_proxy:app`: Specifies the module (`ag_proxy`) and the application object (`app`) within that module.

    Or run it as Systemd-daemon
    ```bash
    sudo cp ag-proxy.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now ag-proxy
    sudo systemctl status ag-proxy
    ```

#### Production (Docker)

For containerized deployments, you can use Docker.

1.  **Build the Docker image:**
    ```bash
    docker build -t ag-proxy .
    ```

2.  **Run the Docker container:**
    ```bash
    docker run -d -p 9099:9099 --env-file .env --name ag-proxy ag-proxy
    ```

    -   `-d`: Runs the container in detached mode (in the background).
    -   `-p 9099:9099`: Maps port 9099 of the host to port 9099 of the container.
    -   `--env-file .env`: Passes environment variables from your `.env` file into the container.
    -   `--name ag-proxy`: The name of the container
    -   `ag-proxy`: The name of the Docker image.

## Configuration

The proxy server uses environment variables for configuration. Currently, the most important one is:

-   `OPENROUTER_API_KEY`: Your API key for Openrouter.
-   `PROXY_ADDRESS`: Listen address
-   `PROXY_PORT`: Listen port

## Support
Don't forget to start my project if you like it.

Contact me at:
- [Facebook](https://fb.me/d1j3y)
- [LinkedIn](https://www.linkedin.com/in/d1j3y/)
- [My Website](https://dijey.pages.dev/)


Made with ❤️ by Dijey