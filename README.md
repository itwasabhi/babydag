# babydag

A very lightweight experiment orchestrator designed for multi-stage ML/RL pipelines. 

The core idea is to define an experiment as a `Plan`, which is directed-acyclic-graph (DAG) of scripts runs that potentially spit out artifacts. Specifying an experiment like this makes it easy to: 
- Run large multi-stage experiments on an HPC
- Re-run portions of the experiment (and downstream components) as needed
- Seperate orchestration from algorithm implementations


For example, orchestrate an experiment to *make dinner*: 
```python
import babydag

class Dinner:
    veggie = "carrot"                             # public class attrs become --flags

    def build(self, plan):
        # add_stage returns a handle: a future for "whatever this stage produces"
        groceries = plan.add_stage(
            "buy_groceries",
            cmds=[f"echo {self.veggie} > {plan.dir('groceries.txt')}"],
            value=plan.dir("groceries.txt"),      # where its output will be
        )
        chopped = plan.add_stage(
            "chop_vegetables",
            deps=[groceries],                     # waits for buy_groceries
            cmds=[f"echo chopped $(cat {groceries.value}) > {plan.dir('chopped.txt')}"],
            value=plan.dir("chopped.txt"),
        )
        cooked = plan.add_stage(
            "cook_vegetables",
            deps=[chopped],
            cmds=[f"echo cooked $(cat {chopped.value}) > {plan.dir('stew.txt')}"],
            value=plan.dir("stew.txt"),
        )
        plan.add_stage(                           # a stage can be a Python function too
            "plate",
            deps=[cooked],
            fn=lambda: print(open(cooked.value).read()),
        )

if __name__ == "__main__":
    babydag.main(Dinner)
```

This experiment can now be driven from the command line as follows
```
uv run python examples/dinner.py status 0 

Dinner_0
========================================================================

    » →  buy_groceries
    » ·  chop_vegetables  ← buy_groceries
    » ·  cook_vegetables  ← chop_vegetables
    » ·  plate  ← cook_vegetables

========================================================================
  4 stages | 0 done | 1 ready | 3 blocked | 4 selected


python dinner.py run 0                          # run everything not yet done
python dinner.py run 0                          # ...run it again: "Nothing to run."
python dinner.py run 0 --only 'cook' --rerun    # redo just the cooking (regex on stage names)

```

Each finished stage leaves a marker file, so a crashed or interrupted run picks up where
it left off. 


## Development

```
uv sync
uv run pytest
uv build
```
