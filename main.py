from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from models.request import OptimizeRequest
from models.response import OptimizeResponse, DirectiveInterpretation, HourlyPlan, BatteryAction
from services.llm_interpreter import interpret_notes
from services.directive_validator import validate_directives
from services.optimizer import optimize
from services.post_validator import post_validate
import os

app = FastAPI(title="GridWise Optimizer", version="1.0.0")

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")


@app.post("/optimize-energy")
def optimize_energy(request: OptimizeRequest):
    try:
        # Step 1: LLM interprets operator notes
        raw_directives = interpret_notes(request.operator_notes)

        # Step 2: Validate LLM output
        valid, msg, cleaned = validate_directives(raw_directives, len(request.operator_notes))
        if not valid:
            raise HTTPException(status_code=422, detail=f"Directive validation failed: {msg}")

        # Step 3: Build effective solar for optimizer
        hours_dicts = [h.model_dump() for h in request.hours]
        battery_dict = request.battery.model_dump()

        # Step 4: Run optimizer
        opt_result = optimize(hours_dicts, battery_dict, cleaned)

        # Step 5: Post-validate
        ok, err = post_validate(opt_result["hourly_plan"], hours_dicts, battery_dict, cleaned)
        if not ok:
            raise HTTPException(status_code=500, detail=f"Post-validation failed: {err}")

        # Step 6: Build response
        interpretation = []
        for d in cleaned:
            interpretation.append(DirectiveInterpretation(
                note_index=d["note_index"],
                applies=d["applies"],
                directive_type=d["directive_type"],
                structured_adjustment=d.get("structured_adjustment"),
                explanation=d.get("explanation", ""),
            ))

        hourly_plan = []
        for entry in opt_result["hourly_plan"]:
            hourly_plan.append(HourlyPlan(
                hour=entry["hour"],
                grid_kwh=entry["grid_kwh"],
                solar_used_kwh=entry["solar_used_kwh"],
                battery_action=entry["battery_action"],
                battery_kwh=entry["battery_kwh"],
                battery_energy_after_kwh=entry["battery_energy_after_kwh"],
            ))

        # Generate summary
        directives_applied = [d for d in cleaned if d["applies"]]
        summary = f"Optimized 24-hour schedule. "
        if directives_applied:
            summary += f"Applied {len(directives_applied)} operator directive(s). "
        summary += f"Total grid cost: {opt_result['total_cost_bdt']:.2f} BDT."

        response = OptimizeResponse(
            scenario_id=request.scenario_id,
            directive_interpretation=interpretation,
            hourly_plan=hourly_plan,
            total_grid_kwh=opt_result["total_grid_kwh"],
            total_cost_bdt=opt_result["total_cost_bdt"],
            peak_grid_kwh=opt_result["peak_grid_kwh"],
            plan_summary=summary,
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
