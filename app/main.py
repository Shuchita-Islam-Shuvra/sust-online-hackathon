import os
import json
import logging
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import uvicorn

from app.schemas import AnalyzeTicketRequest, AnalyzeTicketResponse
from app.llm import run_analysis
from app.config import PORT

# --- Logging Setup ---
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("queuestorm_investigator")

app = FastAPI(
    title="QueueStorm Investigator",
    description="FastAPI Support-Ops Copilot",
    version="1.0.0"
)

# --- Custom Exception Handlers ---

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom exception handler to return:
    - HTTP 400 for malformed JSON or missing required fields (ticket_id, complaint).
    - HTTP 422 for semantically invalid inputs (like empty strings or off-limit enums).
    """
    errors = exc.errors()
    is_missing_or_malformed = False
    
    # Analyze errors to see if they represent a missing field or invalid JSON format
    for error in errors:
        err_type = error.get("type", "")
        loc = error.get("loc", ())
        
        # If it's a parsing error of JSON or a missing required field at root
        if "json_invalid" in err_type or err_type == "missing":
            is_missing_or_malformed = True
            break
        # Check if the missing field is one of the required ones
        if len(loc) > 1 and loc[0] == "body" and loc[1] in ("ticket_id", "complaint") and err_type == "value_error.missing":
            is_missing_or_malformed = True
            break

    status_code = status.HTTP_400_BAD_REQUEST if is_missing_or_malformed else status.HTTP_422_UNPROCESSABLE_ENTITY
    
    # Return brief, non-sensitive JSON error structure without stack trace
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": [
                {
                    "loc": err.get("loc"),
                    "msg": err.get("msg"),
                    "type": err.get("type")
                } for err in errors
            ]
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    Catches all unhandled exceptions and returns a generic HTTP 500 error.
    """
    logger.exception("Unhandled internal exception:")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred."}
    )


# --- Endpoints ---

@app.get("/health")
async def health_check():
    """
    Basic health check endpoint.
    """
    return {"status": "ok"}


@app.post("/analyze-ticket", response_model=AnalyzeTicketResponse)
async def analyze_ticket(request_payload: AnalyzeTicketRequest):
    """
    Analyzes customer complaint and transaction history to produce tickets metadata.
    """
    # Wrap in top-level try/except as final safety net
    try:
        logger.info(f"Received ticket analysis request for ticket_id={request_payload.ticket_id}")
        
        # Run the analysis pipeline (which handles fallbacks internally)
        result_dict, tier_used = await run_analysis(request_payload)
        
        # Log the tier used internally
        if tier_used:
            logger.info(f"Request ticket_id={request_payload.ticket_id} processed by {tier_used}")
        else:
            logger.warning(f"Request ticket_id={request_payload.ticket_id} processed via fallback (all tiers down)")
            
        # Parse into response schema to validate the output format structurally
        response = AnalyzeTicketResponse(**result_dict)
        
        # Try appending to local debug log file logs/tickets.jsonl (ignore errors if folder/file unwritable)
        try:
            log_item = {
                "request": request_payload.model_dump(),
                "response": response.model_dump(),
                "tier_used": tier_used
            }
            with open("logs/tickets.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(log_item, ensure_ascii=False) + "\n")
        except Exception as log_err:
            logger.warning(f"Could not append to logs/tickets.jsonl: {str(log_err)}")
            
        return response

    except HTTPException as http_ex:
        # Re-raise standard HTTP exceptions
        raise http_ex
    except Exception as ex:
        # Log error internally
        logger.exception("Unexpected error in analyze_ticket endpoint:")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during ticket analysis."
        )


if __name__ == "__main__":
    # Programmatic startup matching config PORT
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=False)
