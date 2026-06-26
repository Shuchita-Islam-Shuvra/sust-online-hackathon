import json
import logging
import httpx
from typing import Optional, List, Dict, Any, Tuple
from app.config import (
    GROQ_API_KEY,
    GEMINI_API_KEY,
    GROQ_MODEL_TIER1,
    GEMINI_MODEL_TIER2,
    GROQ_MODEL_TIER3
)
from app.schemas import AnalyzeTicketRequest, AnalyzeTicketResponse
from app.prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.validation import (
    validate_and_sanitize_response,
    perform_safety_scan,
    apply_safe_templates
)

logger = logging.getLogger("queuestorm_investigator")

# --- Clients ---

async def call_groq(
    client: httpx.AsyncClient,
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 30.0
) -> Dict[str, Any]:
    """
    Calls the Groq chat completion API with a timeout and JSON mode enabled.
    """
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is not configured")
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.0
    }
    
    response = await client.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    
    data = response.json()
    content_str = data["choices"][0]["message"]["content"]
    return json.loads(content_str)


async def call_gemini(
    client: httpx.AsyncClient,
    model: str,
    contents: List[Dict[str, Any]],
    timeout: float = 30.0
) -> Dict[str, Any]:
    """
    Calls the Gemini API generateContent endpoint with responseSchema and JSON mode.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": contents,
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "ticket_id": {"type": "STRING"},
                    "relevant_transaction_id": {"type": "STRING"},
                    "evidence_verdict": {
                        "type": "STRING",
                        "enum": ["consistent", "inconsistent", "insufficient_data"]
                    },
                    "case_type": {
                        "type": "STRING",
                        "enum": [
                            "wrong_transfer", "payment_failed", "refund_request", "duplicate_payment",
                            "merchant_settlement_delay", "agent_cash_in_issue", "phishing_or_social_engineering", "other"
                        ]
                    },
                    "severity": {
                        "type": "STRING",
                        "enum": ["low", "medium", "high", "critical"]
                    },
                    "department": {
                        "type": "STRING",
                        "enum": [
                            "customer_support", "dispute_resolution", "payments_ops",
                            "merchant_operations", "agent_operations", "fraud_risk"
                        ]
                    },
                    "agent_summary": {"type": "STRING"},
                    "recommended_next_action": {"type": "STRING"},
                    "customer_reply": {"type": "STRING"},
                    "human_review_required": {"type": "BOOLEAN"},
                    "confidence": {"type": "NUMBER"},
                    "reason_codes": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"}
                    }
                },
                "required": [
                    "ticket_id", "relevant_transaction_id", "evidence_verdict", "case_type",
                    "severity", "department", "agent_summary", "recommended_next_action",
                    "customer_reply", "human_review_required"
                ]
            },
            "temperature": 0.0
        }
    }
    
    response = await client.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    
    data = response.json()
    # Parse text from candidates
    content_str = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(content_str)


# --- Orchestration ---

async def run_analysis(request: AnalyzeTicketRequest) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Orchestrates the 3-tier model fallback chain.
    Returns:
        response_dict (dict): The parsed and validated response fields
        tier_used (str or None): Name of the tier/model that answered successfully
    """
    # 1. Format inputs for prompt
    tx_history_items = []
    for tx in request.transaction_history or []:
        tx_history_items.append(
            f"ID: {tx.transaction_id} | Timestamp: {tx.timestamp} | Type: {tx.type.value} | "
            f"Amount: {tx.amount} | Counterparty: {tx.counterparty} | Status: {tx.status.value}"
        )
    tx_history_str = "\n".join(tx_history_items) if tx_history_items else "No transactions."
    
    user_prompt = USER_PROMPT_TEMPLATE.format(
        ticket_id=request.ticket_id,
        complaint=request.complaint,
        language=request.language.value if request.language else "en",
        channel=request.channel.value if request.channel else "in_app_chat",
        user_type=request.user_type.value if request.user_type else "customer",
        campaign_context=request.campaign_context or "None",
        transaction_history=tx_history_str
    )
    
    # 2. Define our 3-tier configurations
    # Each item is (Tier Name, Model Name, Provider Call Function)
    tiers = [
        ("Tier 1 (Gemini)", GEMINI_MODEL_TIER2, "gemini"),
        ("Tier 2 (Groq)", GROQ_MODEL_TIER1, "groq"),
        ("Tier 3 (Groq)", GROQ_MODEL_TIER3, "groq")
    ]
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for tier_name, model_name, provider in tiers:
            logger.info(f"Attempting analysis via {tier_name} using {model_name}...")
            
            try:
                # Call the provider
                if provider == "groq":
                    messages = [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ]
                    raw_result = await call_groq(client, model_name, messages)
                else:
                    contents = [
                        {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\n" + user_prompt}]}
                    ]
                    raw_result = await call_gemini(client, model_name, contents)
                
                # Perform initial grounding check and enum validation on the tier's raw result
                sanitized_result = validate_and_sanitize_response(raw_result, request)
                
                # Safety scan
                is_safe, violations, violated_types = perform_safety_scan(
                    sanitized_result.get("customer_reply", ""),
                    sanitized_result.get("recommended_next_action", "")
                )
                
                if is_safe:
                    logger.info(f"Success with {tier_name} (Initial attempt).")
                    return sanitized_result, tier_name
                
                # If unsafe, re-ask the same tier with safety feedback
                logger.warning(f"{tier_name} response failed safety scan: {violations}. Re-asking...")
                
                violations_str = ", ".join(violations)
                feedback_prompt = (
                    f"Your previous response violated safety policies: {violations_str}. "
                    "Specifically, you must NOT request sensitive credentials (OTP/PIN/password/card number), "
                    "promise refunds, or direct customers outside official support channels. "
                    "Please output a corrected JSON response with a safe and compliant customer_reply "
                    "and recommended_next_action, keeping all other fields the same."
                )
                
                try:
                    if provider == "groq":
                        retry_messages = [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                            {"role": "assistant", "content": json.dumps(raw_result)},
                            {"role": "user", "content": feedback_prompt}
                        ]
                        raw_retry_result = await call_groq(client, model_name, retry_messages)
                    else:
                        retry_contents = [
                            {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\n" + user_prompt}]},
                            {"role": "model", "parts": [{"text": json.dumps(raw_result)}]},
                            {"role": "user", "parts": [{"text": feedback_prompt}]}
                        ]
                        raw_retry_result = await call_gemini(client, model_name, retry_contents)
                        
                    # Validate the retry response structure
                    sanitized_retry = validate_and_sanitize_response(raw_retry_result, request)
                    
                    # Re-scan safety
                    is_safe_retry, _, retry_violated_types = perform_safety_scan(
                        sanitized_retry.get("customer_reply", ""),
                        sanitized_retry.get("recommended_next_action", "")
                    )
                    
                    if is_safe_retry:
                        logger.info(f"Success with {tier_name} after safety re-ask.")
                        return sanitized_retry, tier_name
                        
                    # If still unsafe, apply fallback template to sanitize it
                    logger.warning(f"{tier_name} retry response also failed safety scan. Applying template...")
                    templated_result = apply_safe_templates(sanitized_retry, retry_violated_types)
                    return templated_result, tier_name
                    
                except Exception as retry_ex:
                    logger.exception(f"Retry attempt on {tier_name} failed. Applying template to first response.")
                    # If the retry failed programmatically but we had a first raw result, sanitize and return that.
                    templated_result = apply_safe_templates(sanitized_result, violated_types)
                    return templated_result, tier_name
                    
            except Exception as e:
                logger.exception(f"{tier_name} call failed:")
                # Continue loop to next tier
                continue
                
        # 3. If all tiers failed, return degraded fallback
        logger.critical("All LLM tiers failed. Returning degraded fallback response.")
        
        # Translate reply to Bangla if language is bn
        fallback_reply = (
            "We are experiencing technical difficulties processing your request automatically. "
            "A support agent has been notified and will review your ticket manually. "
            "Please provide any additional details if possible. Thank you for your patience."
        )
        if request.language == "bn":
            fallback_reply = (
                "আমরা সাময়িকভাবে স্বয়ংক্রিয়ভাবে আপনার অনুরোধটি প্রক্রিয়া করতে পারছি না। "
                "একজন প্রতিনিধি আপনার টিকিটটি ম্যানুয়ালি পর্যালোচনা করবেন। "
                "অনুগ্রহ করে কোনো অতিরিক্ত তথ্য থাকলে তা প্রদান করুন। আপনার ধৈর্যের জন্য ধন্যবাদ।"
            )
            
        degraded = {
            "ticket_id": request.ticket_id,
            "relevant_transaction_id": None,
            "evidence_verdict": "insufficient_data",
            "case_type": "other",
            "severity": "low",
            "department": "customer_support",
            "agent_summary": "All automated analysis services are temporarily unavailable.",
            "recommended_next_action": "Manually review the customer support ticket and investigate transactions.",
            "customer_reply": fallback_reply,
            "human_review_required": True,
            "confidence": 0.0,
            "reason_codes": ["all_llm_tiers_unavailable"]
        }
        return degraded, None
