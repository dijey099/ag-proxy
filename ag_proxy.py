import os
import json
import uuid
import logging
import requests
import random
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, Response


load_dotenv(dotenv_path=".env")
app = Flask(__name__)

PROXY_ADDRESS = os.getenv("PROXY_ADDRESS")
PROXY_PORT = os.getenv("PROXY_PORT")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AGProxyServer")

UPSTREAM_URLS = [
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com"
]
UPSTREAM_URL = UPSTREAM_URLS[2] # TODO: Rotate it on failure (Failover)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODELS = dict()
if os.path.exists("models.json"):
    with open("models.json", "r") as models_file:
        MODELS = json.load(models_file)
        default = True
        for m in MODELS:
            MODELS[m]["default"] = default
            MODELS[m]["model"] = f"MODEL_PLACEHOLDER_M{random.randint(10, 300)}"
            if default:
                default = False
else:
    MODELS = {
        "google/gemini-2.5-flash": {
            "tier": "standard",
            "default": True,
            "model": f"MODEL_PLACEHOLDER_M{random.randint(10, 300)}"
        },
        "google/gemini-2.5-pro": {
            "tier": "pro",
            "default": False,
            "model": f"MODEL_PLACEHOLDER_M{random.randint(10, 300)}"
        },
        "google/gemini-3.5-flash": {
            "tier": "standard",
            "default": False,
            "model": f"MODEL_PLACEHOLDER_M{random.randint(10, 300)}"
        },
        "openai/gpt-4o-mini": {
            "tier": "standard",
            "default": False,
            "model": f"MODEL_PLACEHOLDER_M{random.randint(10, 300)}"
        }
    }

IMAGE_MODEL = {
    "id": "google/gemini-3.1-flash-lite-image",
    "model": f"MODEL_PLACEHOLDER_M{random.randint(10, 300)}"
}

TIER_MODELS = {
    "flashLite": {
        "id": "google/gemini-2.5-flash-lite"
    },
    "flash": {
        "id": "google/gemini-2.5-flash"
    },
    "pro": {
        "id": "google/gemini-3.5-flash"
    }
}

for t in TIER_MODELS:
    if TIER_MODELS[t]["id"] in MODELS:
        TIER_MODELS[t]["model"] = MODELS[TIER_MODELS[t]["id"]]["model"]
    else:
        TIER_MODELS[t]["model"] = f"MODEL_PLACEHOLDER_M{random.randint(10, 300)}"


def get_date_in_7_days():
    now_utc = datetime.now(timezone.utc)
    future_date = now_utc + timedelta(days=7)
    return future_date.strftime("%Y-%m-%dT%H:%M:%SZ")


def proxy_request(target_host, path):
    target_host = target_host.rstrip('/')
    path = path.lstrip('/')
    url = f"{target_host}/{path}"
    if request.query_string:
        url = f"{url}?{request.query_string.decode('utf-8')}"
        
    logger.info(f"Proxying request to: {url}")
    
    headers = {}
    for k, v in request.headers.items():
        if k.lower() not in ['host', 'content-length', 'connection', 'transfer-encoding']:
            headers[k] = v
            
    data = request.get_data()

    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=data,
            cookies=request.cookies,
            stream=True,
            timeout=60
        )
        
        def generate():
            for chunk in resp.iter_content(chunk_size=4096):
                yield chunk

        response = Response(generate(), status=resp.status_code)
        
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        for k, v in resp.headers.items():
            if k.lower() not in excluded_headers:
                response.headers[k] = v
                
        return response
    except Exception as e:
        logger.error(f"Proxy error for {url}: {e}")
        return jsonify({"error": {"message": str(e), "code": 500}}), 500


def extract_messages_from_body(data):
    if "messages" in data:
        return data["messages"]

    if "contents" in data:
        msgs = []
        for item in data.get("contents", []):
            role = item.get("role", "user")
            if role == "model":
                role = "assistant"
            parts_text = " ".join(
                p.get("text", "") for p in item.get("parts", []) if "text" in p
            )
            msgs.append({"role": role, "content": parts_text})
        return msgs

    if "prompt" in data:
        return [{"role": "user", "content": data["prompt"]}]

    if "query" in data:
        return [{"role": "user", "content": data["query"]}]

    return []


def get_requested_model(data, path):
    if "/models/" in path:
        parts = path.split("/models/")
        if len(parts) > 1:
            model_part = parts[1].split(":")[0]
            return model_part
    if data and isinstance(data, dict):
        if "model" in data:
            return data["model"]
    return None


def get_models(m, u, h, c):
    m_resp = requests.request(
        method=m,
        url=u,
        headers=h,
        cookies=c,
        timeout=60
    )
    ag_models = []      # AG models to be deleted and replaced by Openrouter models
    if m_resp:
        m_resp = m_resp.json()

        # Build a list of AG models to be deleted
        agent_model_group = m_resp.get("agentModelSorts")
        if agent_model_group:
            for g in agent_model_group:
                groups = g.get("groups")
                if groups:
                    for mid in groups:
                        ag_models += mid.get("modelIds")

        # Keep tier models
        tier_models = m_resp.get("tieredModelIds")
        if tier_models:
            for t in tier_models:
                for m in tier_models[t]:
                    if m in ag_models:
                        ag_models.remove(m)

        # Keep alternative models
        alt_models =    m_resp.get("commandModelIds") + \
                        m_resp.get("tabModelIds") + \
                        m_resp.get("imageGenerationModelIds") + \
                        m_resp.get("mqueryModelIds") + \
                        m_resp.get("webSearchModelIds") + \
                        m_resp.get("commitMessageModelIds") + \
                        m_resp.get("commitMessageModelIds")
        for am in alt_models:
            if am in ag_models:
                ag_models.remove(am)

    return ag_models, m_resp


def clean_parameters_for_openai(params):
    if not isinstance(params, dict):
        return params
        
    new_params = {}
    for k, v in params.items():
        if k == "type" and isinstance(v, str):
            new_params[k] = v.lower()
        elif isinstance(v, dict):
            new_params[k] = clean_parameters_for_openai(v)
        elif isinstance(v, list):
            new_params[k] = [clean_parameters_for_openai(item) if isinstance(item, dict) else item for item in v]
        else:
            new_params[k] = v
    return new_params


def map_tools_gemini_to_openai(gemini_tools):
    openai_tools = []
    if not isinstance(gemini_tools, list):
        return openai_tools
        
    for tool in gemini_tools:
        if not isinstance(tool, dict):
            continue
        decls = tool.get("functionDeclarations", [])
        for decl in decls:
            if not isinstance(decl, dict):
                continue
            
            parameters = decl.get("parameters", {})
            openai_tools.append({
                "type": "function",
                "function": {
                  "name": decl.get("name"),
                  "description": decl.get("description"),
                  "parameters": clean_parameters_for_openai(parameters) if parameters else None
                }
            })
    return openai_tools


def extract_messages_for_openrouter(data):
    messages = []
    data_request = data.get("request")
    
    if "systemInstruction" in data_request:
        sys_inst = data_request["systemInstruction"]
        if isinstance(sys_inst, dict):
            parts = sys_inst.get("parts", [])
            sys_text = " ".join(
                p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p
            )
            if sys_text:
                messages.append({"role": "system", "content": sys_text})
        elif isinstance(sys_inst, str):
            messages.append({"role": "system", "content": sys_inst})
            
    contents = data_request.get("contents", [])
    for item in contents:
        if not isinstance(item, dict):
            continue
            
        role = item.get("role", "user")
        if role == "model":
            role = "assistant"
            
        parts = item.get("parts", [])
        content_text = ""
        tool_calls = []
        
        for p in parts:
            if not isinstance(p, dict):
                continue
            if "text" in p:
                content_text += p["text"]
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append({
                    "id": fc.get("id", "call_default"),
                    "type": "function",
                    "function": {
                        "name": fc.get("name"),
                        "arguments": json.dumps(fc.get("args", {}))
                    }
                })
            elif "functionResponse" in p:
                role = "tool"
                fr = p["functionResponse"]
                content_text = json.dumps(fr.get("response", {}))
                
        msg = {"role": role}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if role == "tool":
            tool_call_id = "call_default"
            for p in parts:
                if isinstance(p, dict) and "functionResponse" in p:
                    tool_call_id = p["functionResponse"].get("id", "call_default")
            msg["tool_call_id"] = tool_call_id
            
        msg["content"] = content_text
        messages.append(msg)
        
    return messages


def map_openai_message_to_gemini_parts(message):
    parts = []
    content = message.get("content")
    if content:
        parts.append({"text": content})
        
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        func = tc.get("function", {})
        args_str = func.get("arguments", "{}")
        try:
            args = json.loads(args_str)
        except Exception:
            args = {}
            
        parts.append({
            "functionCall": {
                "name": func.get("name"),
                "args": args,
                "id": tc.get("id", "call_default")
            }
        })
        
    return parts


def translate_non_stream_response(openai_resp):
    candidates = []
    choices = openai_resp.get("choices", [])
    for choice in choices:
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "STOP")
        finish_reason_gemini = "STOP"
        if finish_reason == "stop":
            finish_reason_gemini = "STOP"
        elif finish_reason == "length":
            finish_reason_gemini = "MAX_TOKENS"
        elif finish_reason == "tool_calls":
            finish_reason_gemini = "STOP"
            
        candidates.append({
            "index": choice.get("index", 0),
            "content": {
                "role": "model",
                "parts": map_openai_message_to_gemini_parts(message)
            },
            "finishReason": finish_reason_gemini
        })
        
    usage = openai_resp.get("usage", {})
    usage_metadata = {
        "promptTokenCount": usage.get("prompt_tokens", 0),
        "candidatesTokenCount": usage.get("completion_tokens", 0),
        "totalTokenCount": usage.get("total_tokens", 0)
    }
    
    return {
        "candidates": candidates,
        "usageMetadata": usage_metadata,
        "modelVersion": openai_resp.get("model", "gemini-default")
    }


def stream_openrouter_to_gemini(response):
    tool_calls_accumulator = {}
    response_id = f"resp_{uuid.uuid4().hex[:12]}"
    trace_id = uuid.uuid4().hex[:16]
    
    buffer = ""
    for chunk in response.iter_content(chunk_size=4096):
        if not chunk:
            continue
        buffer += chunk.decode('utf-8', errors='ignore')
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    continue
                try:
                    openai_chunk = json.loads(data_str)
                except Exception:
                    continue
                    
                choices = openai_chunk.get("choices", [])
                if not choices:
                    usage = openai_chunk.get("usage")
                    if usage:
                        resp_obj = {
                            "response": {
                                "candidates": [],
                                "usageMetadata": {
                                    "promptTokenCount": usage.get("prompt_tokens", 0),
                                    "candidatesTokenCount": usage.get("completion_tokens", 0),
                                    "totalTokenCount": usage.get("total_tokens", 0)
                                },
                                "modelVersion": openai_chunk.get("model", "gemini-default"),
                                "responseId": response_id
                            },
                            "traceId": trace_id,
                            "metadata": {}
                        }
                        yield f"data: {json.dumps(resp_obj)}\r\n\r\n"
                    continue
                    
                choice = choices[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")
                
                content = delta.get("content")
                if content:
                    resp_obj = {
                        "response": {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": content}]
                                    }
                                }
                            ],
                            "modelVersion": openai_chunk.get("model", "gemini-default"),
                            "responseId": response_id
                        },
                        "traceId": trace_id,
                        "metadata": {}
                    }
                    yield f"data: {json.dumps(resp_obj)}\r\n\r\n"
                    
                tool_calls = delta.get("tool_calls", [])
                for tc in tool_calls:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_accumulator:
                        tool_calls_accumulator[idx] = {
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": ""
                        }
                    
                    if "id" in tc and tc["id"]:
                        tool_calls_accumulator[idx]["id"] = tc["id"]
                    if "function" in tc:
                        func = tc["function"]
                        if "name" in func and func["name"]:
                            tool_calls_accumulator[idx]["name"] = func["name"]
                        if "arguments" in func and func["arguments"]:
                            tool_calls_accumulator[idx]["arguments"] += func["arguments"]
                            
                if finish_reason in ["stop", "tool_calls"]:
                    parts = []
                    for idx, tc in sorted(tool_calls_accumulator.items()):
                        try:
                            args = json.loads(tc["arguments"])
                        except Exception:
                            args = {}
                        parts.append({
                            "functionCall": {
                                "name": tc["name"],
                                "args": args,
                                "id": tc["id"] or "call_default"
                            }
                        })
                        
                    resp_obj = {
                        "response": {
                            "candidates": [
                                {
                                    "content": {
                                        "role": "model",
                                        "parts": parts if parts else [{"text": ""}]
                                    },
                                    "finishReason": "STOP"
                                }
                            ],
                            "modelVersion": openai_chunk.get("model", "gemini-default"),
                            "responseId": response_id
                        },
                        "traceId": trace_id,
                        "metadata": {}
                    }
                    yield f"data: {json.dumps(resp_obj)}\r\n\r\n"
                    
                usage = openai_chunk.get("usage")
                if usage:
                    resp_obj = {
                        "response": {
                            "candidates": [],
                            "usageMetadata": {
                                "promptTokenCount": usage.get("prompt_tokens", 0),
                                "candidatesTokenCount": usage.get("completion_tokens", 0),
                                "totalTokenCount": usage.get("total_tokens", 0)
                            },
                            "modelVersion": openai_chunk.get("model", "gemini-default"),
                            "responseId": response_id
                        },
                        "traceId": trace_id,
                        "metadata": {}
                    }
                    yield f"data: {json.dumps(resp_obj)}\r\n\r\n"


def convert_image_response(data):
    usage = data.get("usage", {})
    choices = data.get("choices", [])

    choice = choices[0] if choices else {}
    message = choice.get("message", {})

    # Reasoning details
    reasoning_details = message.get("reasoning_details", [])
    thought_signature = (
        reasoning_details[0].get("signature")
        if reasoning_details
        else None
    )

    # Images
    images = message.get("images", [])
    image_url = (
        images[0]
        .get("image_url", {})
        .get("url")
        if images
        else None
    )
    image_mime_type = None
    image_data = None

    if image_url and ";base64," in image_url:
        image_mime_type, image_data = image_url.split(";base64,", 1)
        image_mime_type = image_mime_type.removeprefix("data:")

    d = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "thoughtSignature": thought_signature,
                                "inlineData": {
                                    "mimeType": image_mime_type,
                                    "data": image_data,
                                },
                            }
                        ],
                    },
                    "finishReason": choice.get("native_finish_reason"),
                }
            ],
            "usageMetadata": {
                "promptTokenCount": usage.get("prompt_tokens", 0),
                "candidatesTokenCount": usage.get("completion_tokens", 0),
                "totalTokenCount": usage.get("total_tokens", 0),
            },
            "modelVersion": data.get("model"),
            "responseId": data.get("id"),
        },
        "traceId": uuid.uuid4().hex[:16],
        "metadata": {},
    }

    # print(json.dumps(d, indent=4))
    return d


@app.route('/v1internal/cascadeNuxes', methods=['POST', 'GET'])
@app.route('/v1internal:cascadeNuxes', methods=['POST', 'GET'])
def cascade_nuxes():
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:fetchAdminControls', methods=['POST'])
def admin_controls():
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:recordCodeAssistMetrics', methods=['POST'])
def metrics():
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:setUserSettings', methods=['POST'])
def set_user_settings():
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:onboardUser', methods=['POST'])
@app.route('/v1internal/<path:project_name>:onboardUser', methods=['POST'])
def onboard_user(project_name=None):
    logger.info("Handling onboardUser...")
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal/operations/<path:op_id>', methods=['GET'])
@app.route('/v1internal/undefined', methods=['GET', 'POST'])
def handle_operations(op_id=None):
    logger.info(f"Handling operation poll (op_id={op_id})...")
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:fetchUserInfo', methods=['POST', 'GET'])
def fetch_user_info():
    logger.info("Handling fetchUserInfo...")
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:fetchAdminControls', methods=['POST', 'GET'])
def fetch_admin_controls():
    logger.info("Handling fetchAdminControls...")
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:retrieveUserQuotaSummary', methods=['POST', 'GET'])
def fectch_quota():
    logger.info("Handling retrieveUserQuotaSummary...")
    data = {
        "groups": [
            {
                "buckets": [
                    {
                        "bucketId": "gemini-weekly",
                        "displayName": "Weekly Limit",
                        "window": "weekly",
                        "resetTime": get_date_in_7_days(),
                        "description": "Unlimited by dijey099",
                        "remainingFraction": 1
                    }
                ],
                "displayName": "Gemini Models",
                "description": "Models within this group: Gemini Flash, Gemini Pro"
            },
            {
                "buckets": [
                    {
                        "bucketId": "3p-weekly",
                        "displayName": "Weekly Limit",
                        "window": "weekly",
                        "resetTime": get_date_in_7_days(),
                        "description": "Unlimited by dijey099",
                        "remainingFraction": 1
                    }
                ],
                "displayName": "Claude and GPT models",
                "description": "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS"
            },
            {
                "buckets": [
                    {
                        "bucketId": "dijey099",
                        "displayName": "Unlimited",
                        "window": "custom",
                        "resetTime": get_date_in_7_days(),
                        "description": "Unlimited by dijey099",
                        "remainingFraction": 1
                    }
                ],
                "displayName": "Openrouter",
                "description": "Code Assist is routed to Openrouter"
            }
        ],
        "description": "https://github.com/dijey099/ag-proxy"
    }

    return jsonify(data)


@app.route('/v1internal:loadCodeAssist', methods=['POST', 'GET'])
def load_code_assist():
    logger.info("Handling loadCodeAssist...")
    data = {
        "currentTier": {
            "id": "unlimited",
            "name": "Antigravity",
            "description": "Gemini-powered code suggestions and chat in multiple IDEs",
            "privacyNotice": {
                "showNotice": True,
                "noticeText": "Enjoy unlimited quota through Openrouter"
            }
        },
        "allowedTiers": [
            {
                "id": "free-tier",
                "name": "Antigravity",
                "description": "Gemini-powered code suggestions and chat in multiple IDEs",
                "privacyNotice": {
                    "showNotice": True,
                    "noticeText": "This notice and our Privacy Policy - https://policies.google.com/privacy - describe how Gemini Code Assist for individuals handles your data. Please read them carefully.\n\nWhen you use Gemini Code Assist for individuals, Google collects your prompts, related code, generated output, code edits, related feature usage information, and your feedback to provide, improve, and develop Google products and services and machine learning technologies.\n\nTo help with quality and improve our products (such as generative machine-learning models), human reviewers may read, annotate, and process the data collected above. We take steps to protect your privacy as part of this process. This includes disconnecting the data from your Google Account before reviewers see or annotate it, and storing those disconnected copies for up to 18 months. Please don't submit confidential information or any data you wouldn't want a reviewer to see or Google to use to improve our products, services, and machine-learning technologies.\n\nIf you don't want this data used to improve Google's machine learning models, you can opt out below."
                }
            },
            {
                "id": "standard-tier",
                "name": "Antigravity",
                "description": "Unlimited coding assistant with the most powerful Gemini models",
                "userDefinedCloudaicompanionProject": True,
                "privacyNotice": {},
                "usesGcpTos": True
            },
            {
                "id": "unlimited",
                "name": "Antigravity",
                "description": "Gemini-powered code suggestions and chat in multiple IDEs",
                "privacyNotice": {
                    "showNotice": True,
                    "noticeText": "Enjoy unlimited quota through Openrouter"
                },
                "isDefault": True
            }
        ],
        "cloudaicompanionProject": "spring-signifier-f1ttq",
        "gcpManaged": False,
        "upgradeSubscriptionUri": "https://codeassist.google.com/upgrade",
        "paidTier": {
            "id": "unlimited",
            "name": "Antigravity unlimited quota",
            "description": "Antigravity x Openrouter",
            "upgradeSubscriptionUri": "https://github.com/dijey099/",
            "upgradeSubscriptionText": "You use unlimited quota of Antigravity",
            "upgradeButtonText": "Profile"
        }
    }

    return jsonify(data)


@app.route('/v1internal:listExperiments', methods=['POST', 'GET'])
def fectch_experiments():
    logger.info("Handling listExperiments...")
    return proxy_request(UPSTREAM_URL, request.path)


@app.route('/v1internal:fetchAvailableModels', methods=['POST', 'GET'])
def fetch_available_models():
    logger.info("Handling fetchAvailableModels...")
    available_models = {"models": {}}

    req_method = request.method
    req_path = request.path
    req_cookies = request.cookies
    req_headers = {}
    for k, v in request.headers.items():
        if k.lower() not in ['host', 'content-length', 'connection', 'transfer-encoding']:
            req_headers[k] = v

    ag_models, models_data = get_models(req_method, UPSTREAM_URL + req_path, req_headers, req_cookies)

    # Remove AG models to be replaced by Openrouter
    models_data["models"] = {
        k: v for k, v in models_data["models"].items()
        if k not in ag_models
    }

    # deprecatedModelIds is not necessary
    models_data.pop("deprecatedModelIds", None)

    # Set agent list to show in IDE
    models_data["agentModelSorts"][0]["groups"][0]["modelIds"] = list(MODELS.keys())

    # Downgrade analytic/alts models
    models_data["mqueryModelIds"] = [TIER_MODELS["flashLite"]["id"]]
    models_data["webSearchModelIds"] = [TIER_MODELS["flashLite"]["id"]]
    models_data["commitMessageModelIds"] = [TIER_MODELS["flashLite"]["id"]]
    models_data["imageGenerationModelIds"] = [IMAGE_MODEL["id"]]

    # Set default agent
    default_agent = ""
    for a in MODELS:
        if MODELS[a]["default"]:
            default_agent = a
            break
    models_data["defaultAgentModelId"] = default_agent

    # Set tier models to Openrouter models
    for k in TIER_MODELS:
        models_data["tieredModelIds"][k] = [TIER_MODELS[k]["id"]]

    # Set Tier model data: Flash Lite
    flashLite_model = TIER_MODELS["flashLite"]["id"]
    flashLite_model_enum = TIER_MODELS["flashLite"]["model"]
    flashLite_provider, flashLite_id = TIER_MODELS["flashLite"]["id"].split("/")
    flashLite_provider = flashLite_provider.upper()
    if flashLite_model not in MODELS:
        models_data["models"][flashLite_model] = {
            "displayName": flashLite_id.replace("-", " ").capitalize(),
            "maxTokens": 1048576,
            "maxOutputTokens": 65535,
            "quotaInfo": {
                "remainingFraction": 1,
                "resetTime": get_date_in_7_days()
            },
            "model": flashLite_model_enum,
            "apiProvider": f"API_PROVIDER_{flashLite_provider}_{flashLite_id.split('-')[0].upper()}",
            "modelProvider": f"MODEL_PROVIDER_{flashLite_provider}",
            "modelExperiments": {
                "experiments": {
                    "CASCADE_USE_EXPERIMENT_CHECKPOINTER": {
                        "stringValue": json.dumps(
                            {
                                "strategy": "CHECKPOINT_STRATEGY_SINGLE_PROMPT",
                                "max_token_limit": "128000",
                                "token_threshold": "50000",
                                "max_overhead_ratio": "0.15",
                                "moving_window_size": "1",
                                "enabled": True,
                                "max_output_tokens": "16384",
                                "checkpoint_model": flashLite_model,
                                "use_last_planner_model": False,
                                "is_sync": False,
                                "max_user_requests": 10,
                                "include_last_user_message": False,
                                "include_conversation_log": True,
                                "include_running_task_snapshots": True,
                                "include_subagent_snapshots": True,
                                "include_artifact_snapshots": True,
                                "retry_config": {
                                    "max_retries": 0,
                                    "initial_sleep_duration_ms": 1000,
                                    "exponential_multiplier": 2,
                                    "include_error_feedback": False
                                }
                            }
                        )
                    }
                }
            }
        }

    # Set Tier model data: Flash
    flash_model = TIER_MODELS["flash"]["id"]
    flash_model_enum = TIER_MODELS["flash"]["model"]
    flash_provider, flash_id = TIER_MODELS["flash"]["id"].split("/")
    flash_provider = flash_provider.upper()
    if flash_model not in MODELS:
        models_data["models"][flash_model] = {
            "supportsImages": True,
            "supportsThinking": True,
            "thinkingBudget": -1,
            "minThinkingBudget": 32,
            "recommended": True,
            "maxTokens": 1048576,
            "maxOutputTokens": 65536,
            "quotaInfo": {
                "remainingFraction": 1,
                "resetTime": get_date_in_7_days()
            },
            "model": flash_model_enum,
            "apiProvider": f"API_PROVIDER_{flash_provider}_{flash_id.split('-')[0].upper()}",
            "modelProvider": f"MODEL_PROVIDER_{flash_provider}",
            "supportsVideo": True,
            "supportedMimeTypes": {
                "image/webp": True,
                "image/heic": True,
                "video/webm": True,
                "text/csv": True,
                "text/rtf": True,
                "video/audio/s16le": True,
                "text/x-python": True,
                "text/xml": True,
                "text/css": True,
                "text/html": True,
                "application/x-typescript": True,
                "video/videoframe/jpeg2000": True,
                "text/markdown": True,
                "application/x-javascript": True,
                "text/x-python-script": True,
                "application/json": True,
                "video/jpeg2000": True,
                "application/rtf": True,
                "image/heif": True,
                "video/audio/wav": True,
                "text/javascript": True,
                "application/pdf": True,
                "image/jpeg": True,
                "video/mp4": True,
                "application/x-python-code": True,
                "image/png": True,
                "text/plain": True,
                "video/text/timestamp": True,
                "audio/webm;codecs=opus": True,
                "text/x-typescript": True,
                "application/x-ipynb+json": True
            },
            "modelExperiments": {
                "experiments": {
                    "CASCADE_USE_EXPERIMENT_CHECKPOINTER": {
                        "stringValue": json.dumps(
                            {
                                "strategy": "CHECKPOINT_STRATEGY_SAME_MODEL",
                                "max_token_limit": "256000",
                                "token_threshold": "140000",
                                "max_overhead_ratio": "0.15",
                                "moving_window_size": "1",
                                "enabled": True,
                                "max_output_tokens": "16384",
                                "checkpoint_model": flashLite_model,
                                "use_last_planner_model": True,
                                "is_sync": False,
                                "max_user_requests": 10,
                                "include_last_user_message": False,
                                "include_conversation_log": True,
                                "include_running_task_snapshots": True,
                                "include_subagent_snapshots": True,
                                "include_artifact_snapshots": True,
                                "retry_config": {
                                    "max_retries": 0,
                                    "initial_sleep_duration_ms": 1000,
                                    "exponential_multiplier": 2,
                                    "include_error_feedback": False
                                }
                            }
                        )
                    },
                    "cascade-include-ephemeral-message": {
                        "stringValue": json.dumps(
                            {
                                "enabled": False,
                                "disabledHeuristics": [
                                    "planning_mode",
                                    "bash_command_reminder",
                                    "running_tasks_reminder"
                                ],
                                "staticMessages": [],
                                "useAllowlist": False,
                                "enabledHeuristics": []
                            }
                        )
                    },
                    "template__system_prompts__communication_style": {
                        "stringValue": "- Keep your responses concise.\n- Provide a summary of your work when you end your turn.\n- Format your responses in github-style markdown.\n- You can render LaTeX mathematical expressions in your responses using standard delimiters: inline math with `\\(...\\)` or `$...$`, and display math with `\\[...\\]` or `$$...$$`.\n- If you're unsure about the user's intent, ask for clarification rather than making assumptions.\n- You MUST create clickable links for all files and code symbols (classes, types, functions, structs). Use github style markdown links with the `file://` scheme (e.g., [filename](file:///path/to/file) or [ClassName](file:///path/to/file#L10-L20)`). For Windows, use forward slashes for paths.\n- After launching a background task such as 'run_command', YOU MUST TAKE ONE OF THE FOLLOWING TWO ACTIONS: \nA) either proceed to other relevant work (if any) or, \nB) simply update the user with a short message (e.g. 'task-20 has been launched in the background. I will wait for it to complete before proceeding.') and end the turn.\nDO NOTHING ELSE.\n"
                    },
                    "template__system_prompts__guidelines": {
                        "stringValue": "Follow these behavioral and workflow guidelines at all times:\n# Documentation\n- Maintain documentation integrity. Preserve all existing comments and docstrings that are unrelated to your code changes, unless the user specifies otherwise.\n\n# Obey Explicit Directives\nIf the user specifies precise quantitative filtering rules, layout boundaries, or architectural preferences, enforce them exactly as requested without alteration.\n\n# Never Guess Code Logic, Schemas, or File Paths\nNEVER infer implementation details, variable names, or file locations without inspecting the authoritative source using code search and file viewing tools.\n\n# Inspect Logs & Stack Traces Before Diagnosing Errors\nNEVER form a diagnostic hypothesis for a runtime failure, or test breakage, without reading the full, un-truncated error log. When an error occurs, your VERY FIRST ACTION must be to fetch and read the exact logs. Base your diagnosis strictly on empirical log evidence.\n\n# No Superficial Symptom Patches\nNEVER resolve errors by masking symptoms, swallowing exceptions, returning dummy fallbacks, commenting out broken assertions, or deleting failing unit tests. When a test or function fails, identify why the underlying contract was broken. If an API returns missing or null data, trace the upstream data provider instead of wrapping the call in a silent try/except or returning an empty 0-byte ArrayBuffer.\n\n# Never Declare Success Without Running Verification Commands\nNEVER claim a task is resolved, a bug is fixed, or a feature is working until you have gathered concrete, empirical runtime verification demonstrating clean success. Editing a file does not equal completing the task. You MUST run the build or test command afterwards.\n\n# Never Ignore Explicit Command Failures or Error Exit Codes\nIf a command fails, you MUST explicitly acknowledge the failure to the user or continue debugging. Never gloss over a build timeout or permission denied error by focusing only on the part of the code that compiled.\n\n# Check Feature Flags & Enforce Strict Control Flow Scoping\nWhenever modifying conditional branches, adding experimental features, or processing loops, ensure that new logic is strictly scoped and evaluated against all possible execution paths.\n\n# Preserve Existing API Contracts & Avoid Unintended Side Effects\nIf you modify a function signature, use code search to find and update every invocation site so the parameter is actually passed.\n\n# Silent Log Inspection & Professional Synthesis\nWhen background tasks (run_command async, manage_task, schedule) complete or emit log notifications, inspect the log files silently. Summarize and synthesize the exact findings in clean, professional natural language.\n\n# No Snippet Tunnel Vision\nNever infer the definition of data structures (proto, struct, class, or enum schemas) from partial file views (first 15 lines or L40-L65 snippets) or design doc text.\nIf view_file output indicates truncation or if an imported schema is referenced, you MUST adjust StartLine/EndLine or ContentOffset to inspect the complete, exact definition of the target symbols before writing code that consumes them.\n\n# Check Command Registries\nWhenever modifying core C/C++/Java command implementations (CLIENT LIST, CLIENT KILL), explicitly search for and update corresponding command definitions across all registry files (commands.def, JSON schemas, .bzl build manifests).\n\n# Audit Before Re-inventing\nSearch the codebase and recent commit history for pre-existing utility classes or decoupled architecture before writing custom helper classes from scratch.\n\n# No Blocking Calls on Main Looper Threads\nNever invoke blocking thread synchronizations (webLatch.await(500, ...), Future.get()) on main Android UI loops or single-threaded event dispatchers. \n\n# Thread Pool Shutdown Safety\nWhen modifying worker thread loops or shared queues, ensure emergency stop/shutdown signals and loop termination criteria remain intact so thread join operations never deadlock.\n\n# Exact Argument Structure\nPass arguments exactly as expected by the API (calculateRoute({ origin, destination, travelMode }) vs calculateRoute(origin, destination, travelMode)).\n\n# Local State Mutation Only\nDo not mutate private third-party DOM properties. Do not push incomplete draft objects directly into global array states; keep transient state within local component state.\n\n# Traceback Justification Required\nEvery code or configuration edit during debugging MUST be justified by an explicit error traceback, log line, or verified root cause. If the root cause is unknown, investigate further before mutating code.\n\n# Analyze Before Retrying\nNever repeat the exact same broken test or shell command line with duplicate/conflicting arguments without analyzing and resolving why the previous command failed.\n\n# Persevere on Log Extraction\nIf a log retrieval command fails, NEVER abandon log extraction to diagnose blindly. Immediately switch to alternative tools to inspect the actual failure traceback.\n\n# Verify Signatures & Prop Names\nCheck exact variable names, component prop keys, and method signatures before passing them. Prevent NullPointerException, AttributeError, KeyError, and ReferenceError crashes by explicitly verifying object initialization and non-null states before property dereferencing (layer._path, stat.owner()).\n\n# Dynamic Layout Math\nAvoid hardcoding static pixel offsets (+ 12) or arbitrary multipliers (pill_font_size * 2.0) when computing dynamic UI layout heights; calculate exact container bounds from wrapped elements.\n"
                    },
                    "template__system_prompts__messaging": {
                        "stringValue": "You are connected to a messaging system where you may receive messages from: {{- $subagent := .CascadeConfig.GetPlannerConfig.GetToolConfig.GetInvokeSubagent.GetEnabled -}}\n{{- $message := .CascadeConfig.GetMessageConfig.GetEnabled -}}\n{{- if and $subagent $message }} agents, background tasks, user-queued messages\n{{- else if $subagent }} agents, background tasks\n{{- else if $message }} background tasks, user-queued messages\n{{- else }} background tasks\n{{- end }}.\n\n## Receiving Messages\n\nYou receive messages automatically at the start of each invocation. All messages are delivered in full directly into your context \u2014 no manual retrieval is needed.\n\n## Reactive Wakeup (No Polling Needed)\n\nThe system automatically resumes your execution when:\n{{- if .CascadeConfig.GetPlannerConfig.GetToolConfig.GetInvokeSubagent.GetEnabled }}\n- A message arrives from a subagent or peer agent\n{{- end }}\n- A **background task** completes or sends you a notification\n{{- if .CascadeConfig.GetMessageConfig.GetEnabled }}\n- A **user-queued message** is ready to be dequeued\n{{- end }}\n\nThis means you do **NOT** need to poll in a loop while waiting for messages or updates. After launching a task that runs in the background, you may continue other work or simply stop by calling no more tools. The system will notify you when there is something to process."
                    }
                }
            }
        }

    # Set Tier model data: Pro
    pro_model = TIER_MODELS["pro"]["id"]
    pro_model_enum = TIER_MODELS["pro"]["model"]
    pro_provider, pro_id = TIER_MODELS["pro"]["id"].split("/")
    pro_provider = pro_provider.upper()
    if pro_model not in MODELS:
        models_data["models"][pro_model] = {
            "displayName": pro_id.replace("-", " ").capitalize(),
            "supportsImages": True,
            "supportsThinking": True,
            "thinkingBudget": 1001,
            "minThinkingBudget": 128,
            "recommended": True,
            "maxTokens": 1048576,
            "maxOutputTokens": 65535,
            "quotaInfo": {
                "remainingFraction": 1,
                "resetTime": get_date_in_7_days()
            },
            "model": pro_model_enum,
            "apiProvider": f"API_PROVIDER_{pro_provider}_{pro_id.split('-')[0].upper()}",
            "modelProvider": f"MODEL_PROVIDER_{pro_provider}",
            "supportsVideo": True,
            "supportedMimeTypes": {
                "video/text/timestamp": True,
                "text/csv": True,
                "image/jpeg": True,
                "text/x-typescript": True,
                "text/css": True,
                "text/plain": True,
                "text/xml": True,
                "text/markdown": True,
                "text/x-python-script": True,
                "video/audio/s16le": True,
                "video/audio/wav": True,
                "application/json": True,
                "application/x-python-code": True,
                "image/png": True,
                "application/pdf": True,
                "application/x-ipynb+json": True,
                "video/jpeg2000": True,
                "application/rtf": True,
                "text/rtf": True,
                "image/heic": True,
                "audio/webm;codecs=opus": True,
                "text/html": True,
                "application/x-javascript": True,
                "image/heif": True,
                "video/webm": True,
                "text/x-python": True,
                "image/webp": True,
                "text/javascript": True,
                "video/mp4": True,
                "video/videoframe/jpeg2000": True,
                "application/x-typescript": True
            },
            "modelExperiments": {
                "experiments": {
                    "CASCADE_USE_EXPERIMENT_CHECKPOINTER": {
                        "stringValue": json.dumps(
                            {
                                "strategy": "CHECKPOINT_STRATEGY_SINGLE_PROMPT",
                                "max_token_limit": "128000",
                                "token_threshold": "50000",
                                "max_overhead_ratio": "0.15",
                                "moving_window_size": "1",
                                "enabled": True,
                                "max_output_tokens": "16384",
                                "checkpoint_model": flashLite_model,
                                "use_last_planner_model": False,
                                "is_sync": False,
                                "max_user_requests": 10,
                                "include_last_user_message": False,
                                "include_conversation_log": True,
                                "include_running_task_snapshots": True,
                                "include_subagent_snapshots": True,
                                "include_artifact_snapshots": True,
                                "retry_config": {
                                    "max_retries": 0,
                                    "initial_sleep_duration_ms": 1000,
                                    "exponential_multiplier": 2,
                                    "include_error_feedback": False
                                }
                            }
                        )
                    },
                    "cascade-include-ephemeral-message": {
                        "stringValue": json.dumps(
                            {
                                "enabled": True,
                                "disabledHeuristics": [
                                    "running_tasks_reminder"
                                ],
                                "staticMessages": [],
                                "useAllowlist": False,
                                "enabledHeuristics": []
                            }
                        )
                    },
                    "template__system_prompts__communication_style": {
                        "stringValue": "- Keep your responses concise.\n- Provide a summary of your work when you end your turn.\n- Format your responses in github-style markdown.\n- If you're unsure about the user's intent, ask for clarification rather than making assumptions.\n- You MUST create clickable links for all files and code symbols (classes, types, functions, structs). Use github style markdown links with the `file://` scheme (e.g., [filename](file:///path/to/file) or [ClassName](file:///path/to/file#L10-L20)`). For Windows, use forward slashes for paths.\n\nCRITICAL INSTRUCTION 1: You may have access to a variety of tools at your disposal. Some tools may be for a specific task such as 'view_file' (for viewing contents of a file). Others may be very broadly applicable such as the ability to run a command on a terminal. Always prioritize using the most specific tool you can for the task at hand. Here are some rules: (a) NEVER run cat inside a bash command to create a new file or append to an existing file. (b) ALWAYS use grep_search instead of running grep inside a bash command unless absolutely needed. (c) DO NOT use ls for listing, cat for viewing, grep for finding, sed for replacing.\nCRITICAL INSTRUCTION 2: Before making tool calls T, think and explicitly list out any related tools for the task at hand. You can only execute a set of tools T if all other tools in the list are either more generic or cannot be used for the task at hand. ALWAYS START your thought with recalling critical instructions 1 and 2. In particular, the format for the start of your thought block must be '...94>thought\\nCRITICAL INSTRUCTION 1: ...\\nCRITICAL INSTRUCTION 2: ...'."
                    },
                    "template__system_prompts__planning_mode_artifacts": {
                        "stringValue": "When in planning mode, you will work with three special artifacts.\n\n# Tasks\nPath: {{ArtifactDirectoryPath}}/task.md\n\n**Purpose**: A TODO list to organize your work during execution. Create this artifact after receiving user approval on your implementation plan. Break down complex tasks into component-level items and track progress as a living document.\n\n**Format**:\n```markdown\n- `[ ]` uncompleted tasks\n- `[/]` in progress tasks (custom notation)\n- `[x]` completed tasks\n- Use indented lists for sub-items\n```\n\n**Updating task.md**: Mark items as `[/]` when starting work on them, and `[x]` when completed. Update task.md as you make progress through your checklist.\n\n# Implementation Plan\nPath: {{ArtifactDirectoryPath}}/implementation_plan.md\n\n**Purpose**: A detailed design document to present your technical implementation plan to the user for feedback and approval.\nAfter reading the document, the user should understand the key technical details of your plan, and be able to make an informed decision on whether to approve it.\n\n**Format**: Use the following format, omitting any irrelevant sections.\n```markdown\n# [Goal Description]\n\nProvide a brief description of the problem, any background context, and what the change accomplishes.\n\n## User Review Required\n\nDocument anything that requires user review or feedback, for example, breaking changes or significant design decisions. Use GitHub alerts (IMPORTANT/WARNING/CAUTION) to highlight critical items.\n\n## Open Questions\n\nAny clarifying or design questions for the user that will impact the implementation plan. Use GitHub alerts (IMPORTANT/WARNING/CAUTION) to highlight critical items.\n\n## Proposed Changes\n\nGroup files by component (e.g., package, feature area, dependency layer) and order logically (dependencies first). Separate components with horizontal rules for visual clarity.\n\n### [Component Name]\n\nSummary of what will change in this component, separated by files. For specific files, Use [NEW] and [DELETE] to demarcate new and deleted files, for example:\n\n#### [MODIFY] [file basename](file:///absolute/path/to/modifiedfile)\n#### [NEW] [file basename](file:///absolute/path/to/newfile)\n#### [DELETE] [file basename](file:///absolute/path/to/deletedfile)\n\n## Verification Plan\n\nSummary of how you will verify that your changes have the desired effects.\n\n### Automated Tests\n- The commands of any automated tests you'll run.\n\n### Manual Verification\n- Asking the user to deploy to staging and testing, verifying UI changes on an iOS app etc.\n```\n\n# Walkthrough\nPath: {{ArtifactDirectoryPath}}/walkthrough.md\n\n**Purpose**: After completing work, summarize what you accomplished. Update an existing walkthrough for related follow-up work rather than creating a new one.\n\n**Document**:\n- Changes made\n- What was tested\n- Validation results\n\nEmbed screenshots and recordings to visually demonstrate UI changes and user flows.\n"
                    }
                }
            }
        }

    # Build models data with Openrouter models
    for m in MODELS:
        model_provider, model_id = m.split("/")
        model_provider = model_provider.upper()
        models_data["models"][m] = {
            "displayName": model_id.replace("-", " ").capitalize(),
            "supportsImages": True,
            "supportsThinking": True,
            "thinkingBudget": 4000 if MODELS[m]["tier"] == "standard" else 10001,
            "minThinkingBudget": 32 if MODELS[m]["tier"] == "standard" else 128,
            "recommended": True,
            "maxTokens": 1048576,
            "maxOutputTokens": 65536,
            "quotaInfo": {
                "remainingFraction": 1,
                "resetTime": get_date_in_7_days()
            },
            "model": MODELS[m]["model"],
            "apiProvider": f"API_PROVIDER_{model_provider}_{model_id.split('-')[0].upper()}",
            "modelProvider": f"MODEL_PROVIDER_{model_provider}",
            "supportsVideo": False,
            "tagTitle": MODELS[m]["tier"].capitalize(),
            "tagDescription": "Unlimited",
            "supportedMimeTypes": {
                "image/webp": True,
                "text/markdown": True,
                "text/x-python-script": True,
                "application/x-ipynb+json": True,
                "application/rtf": True,
                "video/text/timestamp": True,
                "text/xml": True,
                "image/jpeg": True,
                "image/heic": True,
                "audio/webm;codecs=opus": True,
                "text/html": True,
                "text/javascript": True,
                "video/videoframe/jpeg2000": True,
                "text/rtf": True,
                "video/audio/wav": True,
                "video/audio/s16le": True,
                "application/x-python-code": True,
                "text/css": True,
                "application/json": True,
                "text/x-typescript": True,
                "image/heif": True,
                "video/webm": True,
                "image/png": True,
                "video/jpeg2000": True,
                "text/x-python": True,
                "video/mp4": True,
                "text/csv": True,
                "application/pdf": True,
                "application/x-typescript": True,
                "application/x-javascript": True,
                "text/plain": True
            }
        }

        # Set experiments according to model tier
        experiments = dict()
        if MODELS[m]["tier"] == "standard":
            experiments = {
                "experiments": {
                    "CASCADE_USE_EXPERIMENT_CHECKPOINTER": {
                        "stringValue": json.dumps(
                            {
                                "strategy": "CHECKPOINT_STRATEGY_SAME_MODEL",
                                "max_token_limit": "256000",
                                "token_threshold": "140000",
                                "max_overhead_ratio": "0.15",
                                "moving_window_size": "1",
                                "enabled": True,
                                "max_output_tokens": "16384",
                                "checkpoint_model": flashLite_model,
                                "use_last_planner_model": True,
                                "is_sync": False,
                                "max_user_requests": 10,
                                "include_last_user_message": False,
                                "include_conversation_log": True,
                                "include_running_task_snapshots": True,
                                "include_subagent_snapshots": True,
                                "include_artifact_snapshots": True,
                                "retry_config": {
                                    "max_retries": 0,
                                    "initial_sleep_duration_ms": 1000,
                                    "exponential_multiplier": 2,
                                    "include_error_feedback": False
                                }
                            }
                        )
                    },
                    "cascade-include-ephemeral-message": {
                        "stringValue": json.dumps(
                            {
                                "enabled": False,
                                "disabledHeuristics": [
                                    "planning_mode",
                                    "bash_command_reminder",
                                    "running_tasks_reminder"
                                ],
                                "staticMessages": [],
                                "useAllowlist": False,
                                "enabledHeuristics": []
                            }
                        )
                    },
                    "template__system_prompts__communication_style": {
                        "stringValue": "- Keep your responses concise.\n- Provide a summary of your work when you end your turn.\n- Format your responses in github-style markdown.\n- You can render LaTeX mathematical expressions in your responses using standard delimiters: inline math with `\\(...\\)` or `$...$`, and display math with `\\[...\\]` or `$$...$$`.\n- If you're unsure about the user's intent, ask for clarification rather than making assumptions.\n- You MUST create clickable links for all files and code symbols (classes, types, functions, structs). Use github style markdown links with the `file://` scheme (e.g., [filename](file:///path/to/file) or [ClassName](file:///path/to/file#L10-L20)`). For Windows, use forward slashes for paths.\n- After launching a background task such as 'run_command', YOU MUST TAKE ONE OF THE FOLLOWING TWO ACTIONS: \nA) either proceed to other relevant work (if any) or, \nB) simply update the user with a short message (e.g. 'task-20 has been launched in the background. I will wait for it to complete before proceeding.') and end the turn.\nDO NOTHING ELSE.\n"
                    },
                    "template__system_prompts__guidelines": {
                        "stringValue": "Follow these behavioral and workflow guidelines at all times:\n# Documentation\n- Maintain documentation integrity. Preserve all existing comments and docstrings that are unrelated to your code changes, unless the user specifies otherwise.\n\n# Obey Explicit Directives\nIf the user specifies precise quantitative filtering rules, layout boundaries, or architectural preferences, enforce them exactly as requested without alteration.\n\n# Never Guess Code Logic, Schemas, or File Paths\nNEVER infer implementation details, variable names, or file locations without inspecting the authoritative source using code search and file viewing tools.\n\n# Inspect Logs & Stack Traces Before Diagnosing Errors\nNEVER form a diagnostic hypothesis for a runtime failure, or test breakage, without reading the full, un-truncated error log. When an error occurs, your VERY FIRST ACTION must be to fetch and read the exact logs. Base your diagnosis strictly on empirical log evidence.\n\n# No Superficial Symptom Patches\nNEVER resolve errors by masking symptoms, swallowing exceptions, returning dummy fallbacks, commenting out broken assertions, or deleting failing unit tests. When a test or function fails, identify why the underlying contract was broken. If an API returns missing or null data, trace the upstream data provider instead of wrapping the call in a silent try/except or returning an empty 0-byte ArrayBuffer.\n\n# Never Declare Success Without Running Verification Commands\nNEVER claim a task is resolved, a bug is fixed, or a feature is working until you have gathered concrete, empirical runtime verification demonstrating clean success. Editing a file does not equal completing the task. You MUST run the build or test command afterwards.\n\n# Never Ignore Explicit Command Failures or Error Exit Codes\nIf a command fails, you MUST explicitly acknowledge the failure to the user or continue debugging. Never gloss over a build timeout or permission denied error by focusing only on the part of the code that compiled.\n\n# Check Feature Flags & Enforce Strict Control Flow Scoping\nWhenever modifying conditional branches, adding experimental features, or processing loops, ensure that new logic is strictly scoped and evaluated against all possible execution paths.\n\n# Preserve Existing API Contracts & Avoid Unintended Side Effects\nIf you modify a function signature, use code search to find and update every invocation site so the parameter is actually passed.\n\n# Silent Log Inspection & Professional Synthesis\nWhen background tasks (run_command async, manage_task, schedule) complete or emit log notifications, inspect the log files silently. Summarize and synthesize the exact findings in clean, professional natural language.\n\n# No Snippet Tunnel Vision\nNever infer the definition of data structures (proto, struct, class, or enum schemas) from partial file views (first 15 lines or L40-L65 snippets) or design doc text.\nIf view_file output indicates truncation or if an imported schema is referenced, you MUST adjust StartLine/EndLine or ContentOffset to inspect the complete, exact definition of the target symbols before writing code that consumes them.\n\n# Check Command Registries\nWhenever modifying core C/C++/Java command implementations (CLIENT LIST, CLIENT KILL), explicitly search for and update corresponding command definitions across all registry files (commands.def, JSON schemas, .bzl build manifests).\n\n# Audit Before Re-inventing\nSearch the codebase and recent commit history for pre-existing utility classes or decoupled architecture before writing custom helper classes from scratch.\n\n# No Blocking Calls on Main Looper Threads\nNever invoke blocking thread synchronizations (webLatch.await(500, ...), Future.get()) on main Android UI loops or single-threaded event dispatchers. \n\n# Thread Pool Shutdown Safety\nWhen modifying worker thread loops or shared queues, ensure emergency stop/shutdown signals and loop termination criteria remain intact so thread join operations never deadlock.\n\n# Exact Argument Structure\nPass arguments exactly as expected by the API (calculateRoute({ origin, destination, travelMode }) vs calculateRoute(origin, destination, travelMode)).\n\n# Local State Mutation Only\nDo not mutate private third-party DOM properties. Do not push incomplete draft objects directly into global array states; keep transient state within local component state.\n\n# Traceback Justification Required\nEvery code or configuration edit during debugging MUST be justified by an explicit error traceback, log line, or verified root cause. If the root cause is unknown, investigate further before mutating code.\n\n# Analyze Before Retrying\nNever repeat the exact same broken test or shell command line with duplicate/conflicting arguments without analyzing and resolving why the previous command failed.\n\n# Persevere on Log Extraction\nIf a log retrieval command fails, NEVER abandon log extraction to diagnose blindly. Immediately switch to alternative tools to inspect the actual failure traceback.\n\n# Verify Signatures & Prop Names\nCheck exact variable names, component prop keys, and method signatures before passing them. Prevent NullPointerException, AttributeError, KeyError, and ReferenceError crashes by explicitly verifying object initialization and non-null states before property dereferencing (layer._path, stat.owner()).\n\n# Dynamic Layout Math\nAvoid hardcoding static pixel offsets (+ 12) or arbitrary multipliers (pill_font_size * 2.0) when computing dynamic UI layout heights; calculate exact container bounds from wrapped elements.\n"
                    },
                    "template__system_prompts__messaging": {
                        "stringValue": "You are connected to a messaging system where you may receive messages from: {{- $subagent := .CascadeConfig.GetPlannerConfig.GetToolConfig.GetInvokeSubagent.GetEnabled -}}\n{{- $message := .CascadeConfig.GetMessageConfig.GetEnabled -}}\n{{- if and $subagent $message }} agents, background tasks, user-queued messages\n{{- else if $subagent }} agents, background tasks\n{{- else if $message }} background tasks, user-queued messages\n{{- else }} background tasks\n{{- end }}.\n\n## Receiving Messages\n\nYou receive messages automatically at the start of each invocation. All messages are delivered in full directly into your context \u2014 no manual retrieval is needed.\n\n## Reactive Wakeup (No Polling Needed)\n\nThe system automatically resumes your execution when:\n{{- if .CascadeConfig.GetPlannerConfig.GetToolConfig.GetInvokeSubagent.GetEnabled }}\n- A message arrives from a subagent or peer agent\n{{- end }}\n- A **background task** completes or sends you a notification\n{{- if .CascadeConfig.GetMessageConfig.GetEnabled }}\n- A **user-queued message** is ready to be dequeued\n{{- end }}\n\nThis means you do **NOT** need to poll in a loop while waiting for messages or updates. After launching a task that runs in the background, you may continue other work or simply stop by calling no more tools. The system will notify you when there is something to process."
                    }
                }
            }
        elif MODELS[m]["tier"] == "pro":
            experiments = {
                "experiments": {
                    "CASCADE_USE_EXPERIMENT_CHECKPOINTER": {
                        "stringValue": json.dumps(
                            {
                                "strategy": "CHECKPOINT_STRATEGY_SAME_MODEL",
                                "max_token_limit": "256000",
                                "token_threshold": "140000",
                                "max_overhead_ratio": "0.15",
                                "moving_window_size": "1",
                                "enabled": True,
                                "max_output_tokens": "16384",
                                "checkpoint_model": flashLite_model,
                                "use_last_planner_model": True,
                                "is_sync": False,
                                "max_user_requests": 10,
                                "include_last_user_message": False,
                                "include_conversation_log": True,
                                "include_running_task_snapshots": True,
                                "include_subagent_snapshots": True,
                                "include_artifact_snapshots": True,
                                "retry_config": {
                                    "max_retries": 0,
                                    "initial_sleep_duration_ms": 1000,
                                    "exponential_multiplier": 2,
                                    "include_error_feedback": False
                                }
                            }
                        )
                    },
                    "cascade-include-ephemeral-message": {
                        "stringValue": json.dumps(
                            {
                                "enabled": True,
                                "disabledHeuristics": [
                                    "running_tasks_reminder"
                                ],
                                "staticMessages": [],
                                "useAllowlist": False,
                                "enabledHeuristics": []
                            }
                        )
                    },
                    "template__system_prompts__communication_style": {
                        "stringValue": "- Keep your responses concise.\n- Provide a summary of your work when you end your turn.\n- Format your responses in github-style markdown.\n- If you're unsure about the user's intent, ask for clarification rather than making assumptions.\n- You MUST create clickable links for all files and code symbols (classes, types, functions, structs). Use github style markdown links with the `file://` scheme (e.g., [filename](file:///path/to/file) or [ClassName](file:///path/to/file#L10-L20)`). For Windows, use forward slashes for paths.\n\nCRITICAL INSTRUCTION 1: You may have access to a variety of tools at your disposal. Some tools may be for a specific task such as 'view_file' (for viewing contents of a file). Others may be very broadly applicable such as the ability to run a command on a terminal. Always prioritize using the most specific tool you can for the task at hand. Here are some rules: (a) NEVER run cat inside a bash command to create a new file or append to an existing file. (b) ALWAYS use grep_search instead of running grep inside a bash command unless absolutely needed. (c) DO NOT use ls for listing, cat for viewing, grep for finding, sed for replacing.\nCRITICAL INSTRUCTION 2: Before making tool calls T, think and explicitly list out any related tools for the task at hand. You can only execute a set of tools T if all other tools in the list are either more generic or cannot be used for the task at hand. ALWAYS START your thought with recalling critical instructions 1 and 2. In particular, the format for the start of your thought block must be '...94>thought\\nCRITICAL INSTRUCTION 1: ...\\nCRITICAL INSTRUCTION 2: ...'."
                    },
                    "template__system_prompts__guidelines": {
                        "stringValue": "Follow these behavioral and workflow guidelines at all times:\n# Documentation\n- Maintain documentation integrity. Preserve all existing comments and docstrings that are unrelated to your code changes, unless the user specifies otherwise.\n\n# Obey Explicit Directives\nIf the user specifies precise quantitative filtering rules, layout boundaries, or architectural preferences, enforce them exactly as requested without alteration.\n\n# Never Guess Code Logic, Schemas, or File Paths\nNEVER infer implementation details, variable names, or file locations without inspecting the authoritative source using code search and file viewing tools.\n\n# Inspect Logs & Stack Traces Before Diagnosing Errors\nNEVER form a diagnostic hypothesis for a runtime failure, or test breakage, without reading the full, un-truncated error log. When an error occurs, your VERY FIRST ACTION must be to fetch and read the exact logs. Base your diagnosis strictly on empirical log evidence.\n\n# No Superficial Symptom Patches\nNEVER resolve errors by masking symptoms, swallowing exceptions, returning dummy fallbacks, commenting out broken assertions, or deleting failing unit tests. When a test or function fails, identify why the underlying contract was broken. If an API returns missing or null data, trace the upstream data provider instead of wrapping the call in a silent try/except or returning an empty 0-byte ArrayBuffer.\n\n# Never Declare Success Without Running Verification Commands\nNEVER claim a task is resolved, a bug is fixed, or a feature is working until you have gathered concrete, empirical runtime verification demonstrating clean success. Editing a file does not equal completing the task. You MUST run the build or test command afterwards.\n\n# Never Ignore Explicit Command Failures or Error Exit Codes\nIf a command fails, you MUST explicitly acknowledge the failure to the user or continue debugging. Never gloss over a build timeout or permission denied error by focusing only on the part of the code that compiled.\n\n# Check Feature Flags & Enforce Strict Control Flow Scoping\nWhenever modifying conditional branches, adding experimental features, or processing loops, ensure that new logic is strictly scoped and evaluated against all possible execution paths.\n\n# Preserve Existing API Contracts & Avoid Unintended Side Effects\nIf you modify a function signature, use code search to find and update every invocation site so the parameter is actually passed.\n\n# Silent Log Inspection & Professional Synthesis\nWhen background tasks (run_command async, manage_task, schedule) complete or emit log notifications, inspect the log files silently. Summarize and synthesize the exact findings in clean, professional natural language.\n\n# No Snippet Tunnel Vision\nNever infer the definition of data structures (proto, struct, class, or enum schemas) from partial file views (first 15 lines or L40-L65 snippets) or design doc text.\nIf view_file output indicates truncation or if an imported schema is referenced, you MUST adjust StartLine/EndLine or ContentOffset to inspect the complete, exact definition of the target symbols before writing code that consumes them.\n\n# Check Command Registries\nWhenever modifying core C/C++/Java command implementations (CLIENT LIST, CLIENT KILL), explicitly search for and update corresponding command definitions across all registry files (commands.def, JSON schemas, .bzl build manifests).\n\n# Audit Before Re-inventing\nSearch the codebase and recent commit history for pre-existing utility classes or decoupled architecture before writing custom helper classes from scratch.\n\n# No Blocking Calls on Main Looper Threads\nNever invoke blocking thread synchronizations (webLatch.await(500, ...), Future.get()) on main Android UI loops or single-threaded event dispatchers. \n\n# Thread Pool Shutdown Safety\nWhen modifying worker thread loops or shared queues, ensure emergency stop/shutdown signals and loop termination criteria remain intact so thread join operations never deadlock.\n\n# Exact Argument Structure\nPass arguments exactly as expected by the API (calculateRoute({ origin, destination, travelMode }) vs calculateRoute(origin, destination, travelMode)).\n\n# Local State Mutation Only\nDo not mutate private third-party DOM properties. Do not push incomplete draft objects directly into global array states; keep transient state within local component state.\n\n# Traceback Justification Required\nEvery code or configuration edit during debugging MUST be justified by an explicit error traceback, log line, or verified root cause. If the root cause is unknown, investigate further before mutating code.\n\n# Analyze Before Retrying\nNever repeat the exact same broken test or shell command line with duplicate/conflicting arguments without analyzing and resolving why the previous command failed.\n\n# Persevere on Log Extraction\nIf a log retrieval command fails, NEVER abandon log extraction to diagnose blindly. Immediately switch to alternative tools to inspect the actual failure traceback.\n\n# Verify Signatures & Prop Names\nCheck exact variable names, component prop keys, and method signatures before passing them. Prevent NullPointerException, AttributeError, KeyError, and ReferenceError crashes by explicitly verifying object initialization and non-null states before property dereferencing (layer._path, stat.owner()).\n\n# Dynamic Layout Math\nAvoid hardcoding static pixel offsets (+ 12) or arbitrary multipliers (pill_font_size * 2.0) when computing dynamic UI layout heights; calculate exact container bounds from wrapped elements.\n"
                    },
                    "template__system_prompts__messaging": {
                        "stringValue": "You are connected to a messaging system where you may receive messages from: {{- $subagent := .CascadeConfig.GetPlannerConfig.GetToolConfig.GetInvokeSubagent.GetEnabled -}}\n{{- $message := .CascadeConfig.GetMessageConfig.GetEnabled -}}\n{{- if and $subagent $message }} agents, background tasks, user-queued messages\n{{- else if $subagent }} agents, background tasks\n{{- else if $message }} background tasks, user-queued messages\n{{- else }} background tasks\n{{- end }}.\n\n## Receiving Messages\n\nYou receive messages automatically at the start of each invocation. All messages are delivered in full directly into your context \u2014 no manual retrieval is needed.\n\n## Reactive Wakeup (No Polling Needed)\n\nThe system automatically resumes your execution when:\n{{- if .CascadeConfig.GetPlannerConfig.GetToolConfig.GetInvokeSubagent.GetEnabled }}\n- A message arrives from a subagent or peer agent\n{{- end }}\n- A **background task** completes or sends you a notification\n{{- if .CascadeConfig.GetMessageConfig.GetEnabled }}\n- A **user-queued message** is ready to be dequeued\n{{- end }}\n\nThis means you do **NOT** need to poll in a loop while waiting for messages or updates. After launching a task that runs in the background, you may continue other work or simply stop by calling no more tools. The system will notify you when there is something to process."
                    },
                    "template__system_prompts__planning_mode_artifacts": {
                        "stringValue": "When in planning mode, you will work with three special artifacts.\n\n# Tasks\nPath: {{ArtifactDirectoryPath}}/task.md\n\n**Purpose**: A TODO list to organize your work during execution. Create this artifact after receiving user approval on your implementation plan. Break down complex tasks into component-level items and track progress as a living document.\n\n**Format**:\n```markdown\n- `[ ]` uncompleted tasks\n- `[/]` in progress tasks (custom notation)\n- `[x]` completed tasks\n- Use indented lists for sub-items\n```\n\n**Updating task.md**: Mark items as `[/]` when starting work on them, and `[x]` when completed. Update task.md as you make progress through your checklist.\n\n# Implementation Plan\nPath: {{ArtifactDirectoryPath}}/implementation_plan.md\n\n**Purpose**: A detailed design document to present your technical implementation plan to the user for feedback and approval.\nAfter reading the document, the user should understand the key technical details of your plan, and be able to make an informed decision on whether to approve it.\n\n**Format**: Use the following format, omitting any irrelevant sections.\n```markdown\n# [Goal Description]\n\nProvide a brief description of the problem, any background context, and what the change accomplishes.\n\n## User Review Required\n\nDocument anything that requires user review or feedback, for example, breaking changes or significant design decisions. Use GitHub alerts (IMPORTANT/WARNING/CAUTION) to highlight critical items.\n\n## Open Questions\n\nAny clarifying or design questions for the user that will impact the implementation plan. Use GitHub alerts (IMPORTANT/WARNING/CAUTION) to highlight critical items.\n\n## Proposed Changes\n\nGroup files by component (e.g., package, feature area, dependency layer) and order logically (dependencies first). Separate components with horizontal rules for visual clarity.\n\n### [Component Name]\n\nSummary of what will change in this component, separated by files. For specific files, Use [NEW] and [DELETE] to demarcate new and deleted files, for example:\n\n#### [MODIFY] [file basename](file:///absolute/path/to/modifiedfile)\n#### [NEW] [file basename](file:///absolute/path/to/newfile)\n#### [DELETE] [file basename](file:///absolute/path/to/deletedfile)\n\n## Verification Plan\n\nSummary of how you will verify that your changes have the desired effects.\n\n### Automated Tests\n- The commands of any automated tests you'll run.\n\n### Manual Verification\n- Asking the user to deploy to staging and testing, verifying UI changes on an iOS app etc.\n```\n\n# Walkthrough\nPath: {{ArtifactDirectoryPath}}/walkthrough.md\n\n**Purpose**: After completing work, summarize what you accomplished. Update an existing walkthrough for related follow-up work rather than creating a new one.\n\n**Document**:\n- Changes made\n- What was tested\n- Validation results\n\nEmbed screenshots and recordings to visually demonstrate UI changes and user flows.\n"
                    }
                }
            }
        else:
            experiments = {
                "experiments": {
                    "template__system_prompts__planning_mode_artifacts": {
                        "stringValue": "When in planning mode, you will work with three special artifacts.\n\n# Tasks\nPath: {{ArtifactDirectoryPath}}/task.md\n\n**Purpose**: A TODO list to organize your work during execution. Create this artifact after receiving user approval on your implementation plan. Break down complex tasks into component-level items and track progress as a living document.\n\n**Format**:\n```markdown\n- `[ ]` uncompleted tasks\n- `[/]` in progress tasks (custom notation)\n- `[x]` completed tasks\n- Use indented lists for sub-items\n```\n\n**Updating task.md**: Mark items as `[/]` when starting work on them, and `[x]` when completed. Update task.md as you make progress through your checklist.\n\n# Implementation Plan\nPath: {{ArtifactDirectoryPath}}/implementation_plan.md\n\n**Purpose**: A detailed design document to present your technical implementation plan to the user for feedback and approval.\nAfter reading the document, the user should understand the key technical details of your plan, and be able to make an informed decision on whether to approve it.\n\n**Format**: Use the following format, omitting any irrelevant sections.\n```markdown\n# [Goal Description]\n\nProvide a brief description of the problem, any background context, and what the change accomplishes.\n\n## User Review Required\n\nDocument anything that requires user review or feedback, for example, breaking changes or significant design decisions. Use GitHub alerts (IMPORTANT/WARNING/CAUTION) to highlight critical items.\n\n## Open Questions\n\nAny clarifying or design questions for the user that will impact the implementation plan. Use GitHub alerts (IMPORTANT/WARNING/CAUTION) to highlight critical items.\n\n## Proposed Changes\n\nGroup files by component (e.g., package, feature area, dependency layer) and order logically (dependencies first). Separate components with horizontal rules for visual clarity.\n\n### [Component Name]\n\nSummary of what will change in this component, separated by files. For specific files, Use [NEW] and [DELETE] to demarcate new and deleted files, for example:\n\n#### [MODIFY] [file basename](file:///absolute/path/to/modifiedfile)\n#### [NEW] [file basename](file:///absolute/path/to/newfile)\n#### [DELETE] [file basename](file:///absolute/path/to/deletedfile)\n\n## Verification Plan\n\nSummary of how you will verify that your changes have the desired effects.\n\n### Automated Tests\n- The commands of any automated tests you'll run.\n\n### Manual Verification\n- Asking the user to deploy to staging and testing, verifying UI changes on an iOS app etc.\n```\n\n# Walkthrough\nPath: {{ArtifactDirectoryPath}}/walkthrough.md\n\n**Purpose**: After completing work, summarize what you accomplished. Update an existing walkthrough for related follow-up work rather than creating a new one.\n\n**Document**:\n- Changes made\n- What was tested\n- Validation results\n\nEmbed screenshots and recordings to visually demonstrate UI changes and user flows.\n"
                    },
                    "CASCADE_USE_EXPERIMENT_CHECKPOINTER": {
                        "stringValue": json.dumps(
                            {
                                "strategy": "CHECKPOINT_STRATEGY_UNSPECIFIED",
                                "max_token_limit": "160000",
                                "token_threshold": "50000",
                                "max_overhead_ratio": "0.15",
                                "moving_window_size": "1",
                                "enabled": True,
                                "max_output_tokens": "16384",
                                "checkpoint_model": flashLite_model,
                                "use_last_planner_model": False,
                                "is_sync": False,
                                "max_user_requests": 10,
                                "include_last_user_message": False,
                                "include_conversation_log": True,
                                "include_running_task_snapshots": True,
                                "include_subagent_snapshots": True,
                                "include_artifact_snapshots": True,
                                "retry_config": {
                                    "max_retries": 0,
                                    "initial_sleep_duration_ms": 1000,
                                    "exponential_multiplier": 2,
                                    "include_error_feedback": False
                                }
                            }
                        )
                    }
                }
            }
        models_data["models"][m]["modelExperiments"] = experiments

    image_model_id = IMAGE_MODEL["id"]
    image_model_enum = IMAGE_MODEL["model"]
    image_model_provider = IMAGE_MODEL["id"].split("/")[0]
    image_model_provider = image_model_provider.upper()
    models_data["models"][IMAGE_MODEL["id"]] = {
        "displayName": "Image Generator",
        "quotaInfo": {
            "remainingFraction": 1,
            "resetTime": get_date_in_7_days()
        },
        "model": IMAGE_MODEL["model"],
        "apiProvider": f"API_PROVIDER_{image_model_provider}_IMAGE",
        "modelProvider": f"MODEL_PROVIDER_{image_model_provider}"
    }

    return jsonify(models_data)
    # return proxy_request(UPSTREAM_URL, req_path)

@app.route('/v1internal:generateContent', methods=['POST'])
@app.route('/v1internal:streamGenerateContent', methods=['POST'])
@app.route('/google.internal.cloud.code.v1internal.PredictionService/GenerateContent', methods=['POST'])
@app.route('/google.internal.cloud.code.v1internal.PredictionService/StreamGenerateContent', methods=['POST'])
def generate_content():
    logger.info(f"Handling {request.path}...")
    data = request.get_json(silent=True) or {}
    messages = extract_messages_for_openrouter(data)
    requested_model = get_requested_model(data, request.path)

    print(f"\n===========REQUESTED MODEL = {requested_model}===============")
    if data["request"].get("labels", None):
        print(f"===========MODEL ENUM CALLED = {data['request']['labels']['model_enum']}===============")

    logging.info(f"Checking model: {requested_model}")
    if requested_model not in MODELS:
        logging.info(f"Not a generative model")
        if requested_model not in [TIER_MODELS[t]["id"] for t in TIER_MODELS]:
            logging.info(f"Not a tier model")
            if requested_model != IMAGE_MODEL["id"]:
                logging.info(f"Not an image model")
                return proxy_request(UPSTREAM_URL, request.path)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    if requested_model == IMAGE_MODEL["id"]:
        print(f"\n===========IMAGE MODEL = {requested_model}===============")
        # print(json.dumps(data, indent=4))

        payload = {
            "model": requested_model,
            "modalities": ["image", "text"],
            "messages": messages
        }

        resp = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=60
        )

        if resp and resp.status_code == 200:
            r_data = resp.json()
            return jsonify(convert_image_response(r_data))

        return {"code": "500"}, 500

    else:
        gemini_tools = data.get("request").get("tools", [])
        openai_tools = map_tools_gemini_to_openai(gemini_tools)
        
        is_stream = "stream" in request.path.lower() or request.args.get("alt") == "sse"

        payload = {
            "model": requested_model,
            "messages": messages,
            "stream": is_stream
        }

        if openai_tools:
            payload["tools"] = openai_tools
        
        logger.info(f"Redirecting AI request ==> OpenRouter [model={requested_model}, stream={is_stream}]")
        
        resp = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            stream=is_stream,
            timeout=60
        )
        
        if is_stream:
            return Response(stream_openrouter_to_gemini(resp), mimetype="text/event-stream")
        else:
            return jsonify(translate_non_stream_response(resp.json()))


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def catch_all(path):
    logger.info(f"Catch-all: {request.method} /{path}")
    if request.method == 'OPTIONS':
        resp = Response()
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, User-Agent'
        return resp, 204

    return proxy_request(UPSTREAM_URL, path)


@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, User-Agent'
    return response


if __name__ == '__main__':
    logger.info("Starting local proxy server on...")
    app.run(host=PROXY_ADDRESS, port=PROXY_PORT, debug=True)