# Loom Programming Guide

## Core Concepts

### The `step()` API

```python
result = await step(instruction, context=None, schema=None)
```

**instruction** - What you want the agent to do. Write in natural language as if giving instructions to a capable assistant:

```python
# Good instructions
await step("Read the config file and extract the database URL")
await step("Run the test suite and fix any failing tests")
await step("Analyze the last 10 results and suggest 3 improvements")

# Too vague - be specific about what you want
await step("do something")  # Bad
await step("help")         # Bad
```

**context** - Information this step should know about. Can be any Python object that serializes to a readable format:

```python
# Pass previous results
previous = await step("Generate 5 ideas")
result = await step("Pick the best idea and implement it", context=previous)

# Pass state object
current_state = state.get()
result = await step("Continue the research", context=current_state)

# Pass structured data
context = {
    "experiment_number": 42,
    "previous_results": [0.23, 0.19, 0.31],
    "current_approach": "gradient clipping"
}
result = await step("Run next experiment", context=context)
```

**schema** - When Python code needs to understand the output, provide a schema. The agent will return structured JSON matching this format:

```python
# When you need to branch on results
result = await step(
    "Evaluate the model performance", 
    schema={"accuracy": "float", "loss": "float", "should_continue": "bool"}
)

if result["accuracy"] > 0.95:
    await step("Save this model as the best version")
elif result["should_continue"]:
    await step("Continue training with lower learning rate")
else:
    await step("Start over with different architecture")

# When another step will consume the output, schema not needed
summary = await step("Summarize today's results")
await step(f"Write a report based on: {summary}")
```

## State Management Patterns

### Basic State Operations

```python
# Initialize state at program start
state.set("status", "initializing")
state.update({
    "experiment_count": 0,
    "best_score": None,
    "start_time": time.time()
})

# Read state
current_score = state.get("best_score")
full_state = state.get()

# Update state after results
new_count = state.get("experiment_count") + 1
state.update({
    "experiment_count": new_count,
    "last_experiment_time": time.time()
})
```

### State Conventions

Use consistent naming for common state variables:

```python
# Status tracking
state.set("status", "running")     # "initializing", "running", "paused", "complete", "error"

# Progress tracking  
state.set("iteration", 42)         # Current iteration/step number
state.set("total_iterations", 100) # Total planned iterations

# Best result tracking
state.update({
    "best_score": 0.934,
    "best_config": {"lr": 0.001, "batch_size": 64},
    "best_iteration": 27
})

# Error handling
state.update({
    "error_count": 2,
    "last_error": "Connection timeout",
    "consecutive_failures": 0
})
```

### State for Different Program Types

**Research/Optimization Programs:**
```python
state.update({
    "baseline_score": 0.85,
    "best_score": 0.87,
    "experiments_run": 15,
    "current_strategy": "parameter_sweep",
    "promising_directions": ["increase_depth", "add_dropout"],
    "failed_approaches": ["reduce_lr", "different_optimizer"]
})
```

**Multi-stage Workflows:**
```python
state.update({
    "stage": "data_preparation",  # "prep", "training", "evaluation", "deployment"
    "completed_stages": ["data_download"],
    "stage_results": {
        "data_download": {"files": 12, "total_size": "1.2GB"},
        "data_preparation": {"clean_samples": 10000}
    }
})
```

**Long-running Monitoring:**
```python
state.update({
    "monitoring_since": "2024-03-01T10:00:00Z",
    "total_checks": 1440,
    "alerts_triggered": 3,
    "last_check": "2024-03-02T09:30:00Z",
    "system_status": "healthy"
})
```

## Control Flow Patterns

### Basic Loops

```python
# Simple counting loop
for i in range(10):
    result = await step(f"Run experiment {i+1}")
    state.set("last_experiment", i+1)

# Conditional loop based on results
while True:
    result = await step("Try to improve the model", schema={"score": "float"})
    if result["score"] > 0.95:
        break
    if state.get("attempts", 0) > 20:
        break
    state.set("attempts", state.get("attempts", 0) + 1)

# State-driven loop
while state.get("status") != "complete":
    await step("Continue the task", context=state.get())
    # The step itself updates state.set("status", "complete") when done
```

### Branching Logic

```python
# Branch based on step results
analysis = await step("Analyze the current situation", schema={"category": "str"})

if analysis["category"] == "optimization":
    await step("Focus on hyperparameter tuning")
elif analysis["category"] == "architecture":
    await step("Experiment with model architecture changes")
else:
    await step("Collect more data and retrain")

# Branch based on state
if state.get("error_count", 0) > 3:
    await step("Switch to conservative approach")
    state.set("strategy", "conservative")
else:
    await step("Continue with current aggressive strategy")
```

### Error Handling

```python
# Basic try/catch around steps
try:
    result = await step("Run the risky experiment", schema={"success": "bool"})
    if result["success"]:
        state.set("consecutive_failures", 0)
    else:
        raise Exception("Experiment marked as failed")
except Exception as e:
    error_count = state.get("consecutive_failures", 0) + 1
    state.set("consecutive_failures", error_count)
    
    if error_count > 3:
        await step("Switch to fallback approach")
        state.set("consecutive_failures", 0)
    else:
        await step(f"Retry with simpler approach. Previous error: {e}")

# Graceful degradation
max_retries = 3
for attempt in range(max_retries):
    try:
        result = await step("Attempt the complex operation")
        break  # Success
    except Exception as e:
        if attempt == max_retries - 1:
            # Final attempt failed
            await step("Fall back to simple manual approach")
        else:
            await step(f"Retry {attempt + 1}/{max_retries} with different parameters")
```

### Periodic Replanning

```python
# Replan every N iterations
iteration = 0
while iteration < 1000:
    # Do work
    result = await step("Run next experiment", context=state.get())
    iteration += 1
    state.set("iteration", iteration)
    
    # Replan every 20 iterations
    if iteration % 20 == 0:
        new_strategy = await step(
            "Review the last 20 results and adjust strategy",
            context=state.get(),
            schema={"strategy": "str", "focus_areas": "list"}
        )
        state.update({
            "strategy": new_strategy["strategy"],
            "focus_areas": new_strategy["focus_areas"]
        })

# Time-based replanning
last_replan = time.time()
while True:
    await step("Continue working", context=state.get())
    
    # Replan every hour
    if time.time() - last_replan > 3600:
        await step("Reassess progress and adjust approach")
        last_replan = time.time()
        state.set("last_replan_time", last_replan)
```

## Schema Design Patterns

### When to Use Schema vs String

**Use schema when:**
- Python needs to branch on the result
- You need to extract specific values for calculations
- The result will be used in loops or conditions
- You're aggregating results from multiple steps

**Use string when:**
- The result will be passed to another step as context
- You want natural language summaries or explanations  
- The output is primarily for human reading
- The next consumer is another step() call

```python
# Schema: Python needs to understand the output
metrics = await step("Evaluate the model", schema={
    "accuracy": "float",
    "precision": "float", 
    "recall": "float",
    "is_production_ready": "bool"
})

if metrics["accuracy"] > 0.9 and metrics["is_production_ready"]:
    await step("Deploy the model to production")

# String: Output goes to next step
analysis = await step("Analyze the user feedback")
response = await step(f"Draft a response based on this analysis: {analysis}")
```

### Common Schema Patterns

**Evaluation Results:**
```python
schema = {
    "primary_metric": "float",
    "secondary_metrics": "dict", 
    "is_improvement": "bool",
    "confidence": "float",
    "recommended_action": "str"
}
```

**Decision Making:**
```python
schema = {
    "decision": "str",
    "reasoning": "str",
    "confidence": "float",
    "alternatives_considered": "list",
    "risks": "list"
}
```

**Progress Updates:**
```python
schema = {
    "status": "str",  # "in_progress", "completed", "failed", "blocked"
    "percent_complete": "float",
    "next_steps": "list",
    "blockers": "list",
    "estimated_completion": "str"
}
```

**Research Results:**
```python
schema = {
    "hypothesis": "str",
    "result": "bool",
    "metrics": "dict",
    "insights": "list",
    "follow_up_questions": "list"
}
```

## Advanced Patterns

### Multi-Stage Workflows

```python
async def run_data_pipeline():
    stages = ["extract", "transform", "validate", "load"]
    
    for stage in stages:
        state.set("current_stage", stage)
        
        try:
            result = await step(
                f"Execute {stage} stage of data pipeline",
                context=state.get(),
                schema={"success": "bool", "records_processed": "int", "errors": "list"}
            )
            
            if not result["success"]:
                await step(f"Handle errors in {stage} stage: {result['errors']}")
                continue
            
            # Update stage completion
            completed = state.get("completed_stages", [])
            completed.append(stage)
            state.update({
                "completed_stages": completed,
                f"{stage}_records": result["records_processed"]
            })
            
        except Exception as e:
            await step(f"Critical error in {stage} stage: {e}. Implement recovery.")
            break
    
    state.set("current_stage", "complete")
```

### Adaptive Research

```python
async def adaptive_research():
    # Initialize with multiple strategies
    strategies = ["grid_search", "random_search", "bayesian_opt"]
    strategy_scores = {s: [] for s in strategies}
    
    for round_num in range(10):  # 10 rounds of adaptive research
        state.update({"round": round_num, "strategy_scores": strategy_scores})
        
        # Choose best strategy based on recent performance
        if round_num > 2:
            strategy = await step(
                "Choose the best strategy based on recent performance",
                context=state.get(),
                schema={"chosen_strategy": "str", "reasoning": "str"}
            )["chosen_strategy"]
        else:
            strategy = strategies[round_num % len(strategies)]
        
        state.set("current_strategy", strategy)
        
        # Run experiments with chosen strategy
        results = []
        for exp in range(5):
            result = await step(
                f"Run experiment {exp+1} using {strategy} strategy",
                context=state.get(),
                schema={"score": "float", "config": "dict"}
            )
            results.append(result["score"])
        
        # Update strategy performance
        strategy_scores[strategy].extend(results)
        avg_score = sum(results) / len(results)
        
        await step(f"Strategy {strategy} achieved average score {avg_score}")
```

### Self-Modifying Programs

```python
async def self_improving_program():
    iteration = 0
    
    while iteration < 100:
        # Run current version
        result = await step("Execute current research approach", schema={"score": "float"})
        
        iteration += 1
        state.update({"iteration": iteration, "last_score": result["score"]})
        
        # Every 10 iterations, consider modifying the approach
        if iteration % 10 == 0:
            improvement = await step(
                "Analyze recent performance. Should we modify our approach?",
                context=state.get(),
                schema={"should_modify": "bool", "proposed_changes": "str"}
            )
            
            if improvement["should_modify"]:
                await step(
                    f"Implement these changes to our approach: {improvement['proposed_changes']}",
                    context=state.get()
                )
                
                # Could even modify this program file and restart
                await step("Update the program code based on learned improvements")
```

## Debugging and Monitoring

### Logging Best Practices

```python
# Log important state changes
async def log_state_change(description):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    await step(f"[{timestamp}] {description}")
    
# Use throughout your program
await log_state_change("Starting optimization phase")
state.set("phase", "optimization")

result = await step("Run optimization", schema={"improvement": "float"})
await log_state_change(f"Optimization complete: {result['improvement']} improvement")
```

### State Snapshots

```python
# Periodic state snapshots for debugging
if iteration % 25 == 0:
    snapshot = {
        "iteration": iteration,
        "state": state.get(),
        "timestamp": time.time()
    }
    
    with open(f"snapshot_{iteration}.json", "w") as f:
        json.dump(snapshot, f, indent=2)
    
    await step(f"Saved state snapshot at iteration {iteration}")
```

### Recovery Patterns

```python
# Check for crash recovery on startup
if os.path.exists("loom-state.json"):
    current_state = state.get()
    if current_state.get("status") == "running":
        # Previous run was interrupted
        await step(
            "Detected interrupted run. Assess situation and resume appropriately.",
            context=current_state
        )
        
        # Resume from where we left off
        iteration = current_state.get("iteration", 0)
    else:
        iteration = 0
else:
    # Fresh start
    iteration = 0
```

Remember: loom programs are meant to run for a long time and be resilient. Design for interruption, resumption, and adaptation.