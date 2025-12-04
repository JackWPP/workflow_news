import httpx
import json
import logging

# Configuration
COZE_API_URL = "https://api.coze.cn/v1/workflow/stream_run"
WORKFLOW_ID = "7579472848718069801"
ACCESS_TOKEN = "pat_Ynbf36hlJV5G8qSXSOzwsLWo4iYKLLHLM66GDWnipEEQysUTFrhdA96EZLCPgHSL"

logger = logging.getLogger(__name__)

async def fetch_coze_news():
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "workflow_id": WORKFLOW_ID,
        "parameters": {}, 
    }

    full_content = ""

    try:
        # Use a long timeout because workflow generation can take time
        async with httpx.AsyncClient(timeout=300.0) as client:
            logger.info(f"Sending request to Coze: {COZE_API_URL}")
            # Not using stream=True, waiting for full response
            response = await client.post(COZE_API_URL, headers=headers, json=payload)
            
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
