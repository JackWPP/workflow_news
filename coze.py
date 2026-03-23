import httpx
import json
import logging
import os
from pathlib import Path

# Configuration
DEFAULT_COZE_API_URL = "https://api.coze.cn/v1/workflow/stream_run"
DEFAULT_WORKFLOW_ID = "7579472848718069801"

logger = logging.getLogger(__name__)


def load_dotenv(dotenv_path: str = ".env") -> None:
    env_file = Path(dotenv_path)
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        os.environ.setdefault(key, value)


load_dotenv()


def get_coze_config():
    api_url = os.getenv("COZE_API_URL", DEFAULT_COZE_API_URL)
    workflow_id = os.getenv("COZE_WORKFLOW_ID", DEFAULT_WORKFLOW_ID)
    access_token = os.getenv("COZE_ACCESS_TOKEN")
    return api_url, workflow_id, access_token

async def fetch_coze_news():
    api_url, workflow_id, access_token = get_coze_config()

    if not access_token:
        logger.error("Missing COZE_ACCESS_TOKEN. Set it in the environment or .env file.")
        return None

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "workflow_id": workflow_id,
        "parameters": {}, 
    }

    full_content = ""

    try:
        # Use a long timeout because workflow generation can take time
        async with httpx.AsyncClient(timeout=300.0) as client:
            logger.info(f"Sending request to Coze: {api_url}")
            # Not using stream=True, waiting for full response
            response = await client.post(api_url, headers=headers, json=payload)
            
            logger.info(f"Response status: {response.status_code}")
            
            # Force UTF-8 decoding
            text = response.content.decode('utf-8')

            if response.status_code != 200:
                logger.error(f"Coze API Error: {response.status_code} - {text}")
                return None

            lines = text.split('\n')
            
            current_event = None
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                    logger.debug(f"Event detected: {current_event}")
                elif line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    logger.debug(f"Data detected for event {current_event}: {data_str[:50]}...")
                    
                    if not current_event:
                        continue
                        
                    event_type = current_event.lower()
                    
                    if event_type == "message":
                        try:
                            data_json = json.loads(data_str)
                            if "content" in data_json:
                                chunk = data_json["content"]
                                full_content += chunk
                                logger.debug(f"Collected chunk: {chunk[:20]}...")
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode JSON data: {data_str}")
                    
                    elif event_type == "error":
                         try:
                            data_json = json.loads(data_str)
                            logger.error(f"Workflow Error: {data_json.get('error_message', 'Unknown error')}")
                         except:
                             logger.error(f"Workflow Error (raw): {data_str}")
                    
                    elif event_type == "done":
                        logger.info("Workflow completed (done event received).")
                        
    except Exception as e:
        logger.exception("Error fetching news from Coze")
        return None

    if not full_content:
        logger.warning("Warning: Fetched content is empty.")
    
    return full_content
