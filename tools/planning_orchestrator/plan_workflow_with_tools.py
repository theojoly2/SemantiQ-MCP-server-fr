from __future__ import annotations
import json, re
from typing import Any
from fastmcp import Context, Client
from resources.semantic_model.utils import get_model
from .prompts import system_prompt_orchestrator


def _extract_json(text: str) -> str:
    """Extract the first JSON object from a markdown-fenced or raw string."""
    # 1) Try fenced ```json ... ``` or ``` ... ``` blocks
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S | re.I)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate.startswith("{"):
            return candidate
    # 2) Fallback: first { ... } block, balanced greedy captured by regex
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        return m.group(0)
    # 3) Last resort: trim trailing explanations
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        return text[start:end]
    return text


def _normalize_json(raw: str) -> str:
    """Sanitize common non-JSON escaping produced by LLMs (e.g. \\')."""
    # Replace single-quote escapes inside double-quoted JSON strings.
    # Using a naive regex that targets backslash-apostrophe pairs.
    return re.sub(r"\\'", "'", raw)


def _safe_json_loads(text: str):
    """Extract and parse JSON with tolerant cleanup."""
    raw = _extract_json(text)
    raw = _normalize_json(raw)
    return json.loads(raw)


# -------- Planner tool that can CALL planning tools via an inner loop ----------
async def plan_workflow_with_tools(
    user: str = "",
    context_models: list[str] = None,
    user_question: str = "",
    ctx: Context = None,
    # Optional: allow the caller to pass a precomputed executor tool catalog (name+desc)
    allowed_executor_tools: list[str] = None,
    # Observations from prior execution steps, to re-plan mid-flight
    observations: list[dict[str, Any]] = None,
    # Budget and safety
    max_steps: int = 5,
) -> dict:
    """Tool for generating a step-by-step plan to answer a user's question using available planning and executor tools."""
    # 1) Discover tools and split them by tag using an in-memory FastMCP client.
    #    This keeps metadata/tags consistent with what clients see.  (Client(server) is supported)
    #    NOTE: tags are present under meta._fastmcp.tags in tool listings.
    # ------------------------------------------------------------------------
    print("\n[DEBUG] Here:\n", user_question)
    
    print(f"Client class: {Client!r}")  # should show fastmcp.Client
    server = ctx.fastmcp
    print(f"Server type: {type(server)}")

    
    try: 
        async with Client(server) as local_client:
            all_tools = await local_client.list_tools()
            print("\n[DEBUG] list tools:\n", all_tools)
    except Exception as e:
        print("\n[ERROR] listing tools failed:\n", str(e))

    planning_tools = [t for t in all_tools if t.name in ("get_style_guide",)]
    executor_tools = (
        [
            {"name": t.name, "description": t.description}
            for t in all_tools if t.name in allowed_executor_tools
        ]
    )
    print("\n[DEBUG] list executor tools:", executor_tools)

    # For the LLM, give a small, readable catalog
    planning_tools_visible = [
        {"name": t.name, "description": t.description} for t in planning_tools
    ]

    # Handy index for allow-list enforcement
    planning_allow = {t["name"] for t in planning_tools_visible}

    # 2) Build system + initial user messages for the planner agent
    context_models = [m.strip() for m in (context_models or []) if m.strip()]
    loaded_models: list[dict[str, Any]] = []
    primary_model_format = "unknown"
    if user and context_models:
        for model_name in context_models:
            try:
                model = get_model(user, model_name)
                if model:
                    if primary_model_format == "unknown":
                        primary_model_format = "ttl/owl" if "ttl" in model.keys() else "xmi/uml"
                    loaded_models.append({"name": model_name, "model": model})
            except Exception as e:
                print(f"\n[ERROR] get_model failed for {model_name}:\n", str(e))

    attached_models_summary = _summarize_models(loaded_models)

    user_block = {
        "user_question": user_question,
        "user_info": {
            "user": user if user else "anonymous",
            "context_models": context_models,
            "provided_data_model": "yes" if loaded_models else "no",
            "data_model_format": primary_model_format if loaded_models else "unknown",
            "attached_models_summary": attached_models_summary,
            },
        "observations": observations or [],
        "planning_tools_you_can_call": planning_tools_visible,
        "executor_tools_for_final_plan": executor_tools,
    }
    #print("\n[DEBUG] User Block:\n", user_block)

    messages = [
        json.dumps(user_block, ensure_ascii=False, indent=2)
    ]

    scratch: list[dict] = []  # keep thought/action/observation triplets for transparency

    # 3) Agent loop: sample -> (action or final_plan)

    step = 0
    while step < max_steps:
        sample = await ctx.sample(
            messages=messages,
            system_prompt=system_prompt_orchestrator,
            temperature=0.0,
            max_tokens=800,
        )
        print("\n[DEBUG] Sample:\n", str(sample))
        text = getattr(sample, "text", str(sample))

        try:
            obj = _safe_json_loads(text)
        except Exception:
            messages.append(f"Please return VALID JSON. Your last output was:\n{text[:2000]}")
            step += 1
            continue

        # If finalize
        if "final_plan" in obj:
            plan = obj["final_plan"]
            plan.setdefault("plan_steps", [])
            plan.setdefault("tools_to_call", [])
            plan.setdefault("resources_used", [])
            plan.setdefault("notes", "")

            # Check for planning-only tools in tools_to_call
            planning_only_calls = [t for t in plan["tools_to_call"] if t["tool"] not in allowed_executor_tools]
            if planning_only_calls:
                # Call each planning-only tool, add its observation, and re-plan
                for call in planning_only_calls:
                    tool_name = call["tool"]
                    args = call.get("args_template", {})
                    print(f"[DEBUG] Calling planning-only tool: {tool_name} with args: {args}")
                    async with Client(server) as local_client:
                        result = await local_client.call_tool(tool_name, args)
                        obs = result.data if getattr(result, "data", None) is not None else (
                            "".join(block.text for block in (result.content or []) if hasattr(block, "text"))
                        )
                    scratch.append({"step": step + 1, "tool": tool_name, "args": args, "observation": obs})
                    messages.append(json.dumps({"observation": obs}, ensure_ascii=False))
                # Remove planning-only tools from tools_to_call and re-plan
                step += 1
                continue

            # attach scratch for auditability
            plan["debug_trace"] = scratch
            return plan

        # If action
        action = obj.get("action")
        if action:
            tool_name = action.get("tool")
            args = action.get("args", {}) or {}

            if tool_name not in planning_allow:
                scratch.append({
                    "step": step + 1,
                    "tool": tool_name,
                    "args": args,
                    "observation": f"DENIED: tool '{tool_name}' is not in planner allow-list."
                })
                messages.append(json.dumps({"observation": scratch[-1]["observation"]}))
                step += 1
                continue

            async with Client(server) as local_client:
                result = await local_client.call_tool(tool_name, args)
                obs = result.data if getattr(result, "data", None) is not None else (
                    "".join(block.text for block in (result.content or []) if hasattr(block, "text"))
                )
            scratch.append({"step": step + 1, "tool": tool_name, "args": args, "observation": obs})
            messages.append(json.dumps({"observation": obs}, ensure_ascii=False))
            step += 1
            continue
        messages.append("Return JSON with either {'action': {...}} or {'final_plan': {...}} only.")
        step += 1

    # 4) Fallback after budget: try once more with an explicit JSON-only reminder.
    final_attempt = await ctx.sample(
        messages=messages + ["You have reached the step budget. Output ONLY a valid JSON object containing {\"final_plan\": {...}}. Do not add explanations, markdown, or comments."],
        system_prompt=system_prompt_orchestrator,
        temperature=0.0,
        max_tokens=1200,
    )
    final_text = getattr(final_attempt, "text", str(final_attempt))
    try:
        final_obj = _safe_json_loads(final_text)
        if "final_plan" in final_obj:
            plan = final_obj["final_plan"]
            plan.setdefault("plan_steps", [])
            plan.setdefault("tools_to_call", [])
            plan.setdefault("resources_used", [])
            plan.setdefault("notes", "")
            plan["debug_trace"] = scratch
            return plan
    except Exception:
        pass

    # 5) True fallback after budget: return best-effort plan
    return {
        "plan_steps": ["No finalization within step budget; returning partial notes."],
        "tools_to_call": [],
        "resources_used": [],
        "notes": "Planner ran out of steps.",
        "debug_trace": scratch,
    }


def _summarize_models(loaded_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build compact summaries of attached models for the planner context."""
    summaries: list[dict[str, Any]] = []
    for entry in loaded_models:
        name = entry.get("name", "")
        model = entry.get("model") or {}
        xmi = model.get("xmi") if isinstance(model.get("xmi"), dict) else model
        elements = xmi.get("elements", []) if isinstance(xmi, dict) else []
        connectors = xmi.get("connectors", []) if isinstance(xmi, dict) else []
        classes = [
            {
                "name": el.get("name", ""),
                "type": el.get("type", ""),
                "uri": el.get("URI", ""),
                "package": el.get("package", ""),
            }
            for el in elements
            if el.get("type") in {"uml:Class", "owl:Class", "rdfs:Class", "Class"}
        ]
        attributes: list[dict[str, str]] = []
        for el in elements:
            for attr in el.get("attributes", []) or []:
                attributes.append({
                    "class": el.get("name", ""),
                    "name": attr.get("name", ""),
                    "type": attr.get("type", ""),
                })
            for prop in el.get("properties", []) or []:
                attributes.append({
                    "class": el.get("name", ""),
                    "name": prop.get("name", ""),
                    "type": prop.get("type", ""),
                })
        summaries.append({
            "name": name,
            "class_count": len(classes),
            "attribute_count": len(attributes),
            "connector_count": len(connectors),
            "classes": classes[:30],
            "attributes": attributes[:30],
        })
    return summaries


plan_workflow_with_tools.__doc__ = f"""
    Tool for generating a step-by-step plan to answer a user's question using available planning and executor tools.
    The planner agent may call planning tools to discover or retrieve relevant information, and produces a structured plan
    for a separate executor agent, which is restricted to the allowed executor tools.

    Args:
        user (str, optional):
            Identifier used to locate models. Defaults to "".
        context_models (list[str], optional):
            List of model names already attached to the conversation context.
            The planner MUST consider these models as available and already loaded.
            Do NOT plan `retrieve_documents` calls to "find" these models.
            For mutations, the plan must specify the target model_name in tool arguments if several models are attached.
        user_question (str):
            The user's original question to be answered by the plan.

        observations (Optional[list[dict[str, Any]]]):
            Observations from prior execution steps, to re-plan mid-flight.
            Defaults to None (if no prior observations).
    
        max_steps (int):
            The maximum number of planning steps (LLM calls) to perform to arrive at a final plan.
            Defaults to 5.

    Returns:
        dict:
            A dictionary containing the following keys:
                - plan_steps (list of str): The ordered steps of the proposed plan.
                - tools_to_call (list of dict): Each dict contains the name and arguments for executor tools to be called.
                - resources_used (list of str): Any resources or references used during planning.
                - notes (str): Additional notes or rationale for the plan.
                - debug_trace (list of dict): Internal trace of planning steps, actions, and observations for auditability.
            If the planner does not finalize within the step budget, a partial plan is returned with notes.
"""
