"""LLM usage accounting and tenant daily circuit breaker. No provider call is made here."""
from __future__ import annotations
import os
from config_loader import env
from security_schema import add_api_usage,daily_cost

def reserve_ai_budget(conn,tenant_id:str,user_id:str='',requested_estimate_usd:float=0.0)->dict:
 limit=float(env('AI_DAILY_COST_LIMIT_USD','1.00'))
 spent=daily_cost(conn,tenant_id,user_id)
 requested=max(0.0,float(requested_estimate_usd))
 allowed=spent+requested<=limit
 return {'allowed':allowed,'tenant_id':tenant_id,'spent_usd':spent,'requested_usd':requested,'limit_usd':limit,'remaining_usd':max(0.0,limit-spent)}

def record_llm_usage(conn,tenant_id:str,user_id:str,provider:str,model_used:str,prompt_tokens:int,completion_tokens:int,estimated_cost_usd:float,metadata:dict|None=None)->dict:
 limit=float(env('AI_DAILY_COST_LIMIT_USD','1.00'))
 before=daily_cost(conn,tenant_id,user_id)
 if before+float(estimated_cost_usd)>limit:
  return {'recorded':False,'circuit_open':True,'daily_spend_usd':before,'limit_usd':limit}
 add_api_usage(conn,provider,model_used,prompt_tokens,completion_tokens,estimated_cost_usd,user_id,tenant_id,metadata)
 after=daily_cost(conn,tenant_id,user_id)
 return {'recorded':True,'circuit_open':after>=limit,'daily_spend_usd':after,'limit_usd':limit}
