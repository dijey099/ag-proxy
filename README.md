
# AG Proxy

This project provides an Antigravity proxy that intercepts AG IDE requests and routes AI Code assist requests to Openrouter. It consists of two main components:

- [`ag_patch.py`](file:///home/sniper099/Music/AG_Proxy/ag_patch.py): A script to patch the Antigravity IDE to route traffic through the local proxy.
- [`ag_proxy.py`](file:///home/sniper009/Music/AG_Proxy/ag_proxy.py): The proxy server that handles requests from the AG IDE and forwards them to Openrouter.

## Table of Contents

- [AG Proxy](#ag-proxy)
  - [Overview](#overview)
  - [Installation](#installation)
  - [Usage](#usage)
    - [Patcher Script (`ag_patch.py`)](#patcher-script-ag_patch.py)
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
    cd antigravity-proxy-tools
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

### Patcher Script (`ag_patch.py`)

The `ag_patch.py` script modifies your Antigravity IDE configuration to direct its AI code assist traffic to the local proxy server.

**Command-line Interface (CLI) Options:**

```bash
python ag_patch.py --help
```

```
usage: ag_patch.py [-h] [-u] [--host HOST] [--port PORT]

Antigravity IDE Patcher

options:
  -h, --help            show this help message and exit
  -u, --unpatch         Unpatch the Antigravity IDE (revert changes)
  --host HOST           Host for the proxy (default: localhost)
  --port PORT           Port for the proxy (default: 8000)
```

**Patching the IDE:**

To patch the IDE to use the proxy running on `localhost:9099` (default):

```bash
python ag_patch.py --patch <AG IDE dictory path>
```

To specify a different host and port:

```bash
python ag_patch.py --url http://192.168.1.100:9099 --patch <AG IDE dictory path>
```

**Unpatching the IDE:**

To revert the changes made by the patcher:

```bash
python ag_patch.py --unpatch <AG IDE dictory path>
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
    Assuming your `ag_proxy.py` file contains an `app` object (e.g., a Flask or FastAPI app), you can run it with Gunicorn:

    ```bash
    gunicorn -w 4 -b 0.0.0.0:9099 ag_proxy:app
    ```

    -   `-w 4`: Runs 4 worker processes. Adjust based on your server's resources.
    -   `-b 0.0.0.0:9099`: Binds the server to all network interfaces on port 9099.
    -   `ag_proxy:app`: Specifies the module (`ag_proxy`) and the application object (`app`) within that module.

#### Production (Docker)

For containerized deployments, you can use Docker. A `Dockerfile` should be created to build the image.

(Assuming you have a `Dockerfile` in your project root, e.g., provided by the agent previously).

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
