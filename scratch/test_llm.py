import asyncio
import json
import os
import sys

# Add workspace directory to python path
sys.path.insert(0, "/Users/raihri/Desktop/sust-online-hackathon")

from app.schemas import AnalyzeTicketRequest, TransactionHistoryItem, LanguageEnum, ChannelEnum, UserTypeEnum, TransactionTypeEnum, TransactionStatusEnum
from app.llm import run_analysis

async def test():
    # Construct request similar to SAMPLE-03
    req = AnalyzeTicketRequest(
        ticket_id="TKT-003",
        complaint="I tried to pay 1200 taka for my mobile recharge but the app showed failed. But my balance was deducted! Please refund my money.",
        language=LanguageEnum.EN,
        channel=ChannelEnum.IN_APP_CHAT,
        user_type=UserTypeEnum.CUSTOMER,
        campaign_context=None,
        transaction_history=[
            TransactionHistoryItem(
                transaction_id="TXN-9301",
                timestamp="2026-04-14T16:00:00Z",
                type=TransactionTypeEnum.PAYMENT,
                amount=1200.0,
                counterparty="MERCHANT-MOBILE-OP",
                status=TransactionStatusEnum.FAILED
            )
        ]
    )
    
    # We want to enable logging to see the exception trace
    import logging
    logging.basicConfig(level=logging.INFO)
    
    res, tier = await run_analysis(req)
    print("\n--- RESULTS ---")
    print("Tier used:", tier)
    print("Response:", json.dumps(res, indent=2))

if __name__ == "__main__":
    asyncio.run(test())
