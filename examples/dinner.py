"""Make dinner: buy groceries -> chop -> cook -> plate.

    uv run python examples/dinner.py status 0
    uv run python examples/dinner.py run 0
    uv run python examples/dinner.py run 0 --veggie onion
"""

import babydag


class Dinner:
    veggie = "carrot"  # public class attrs become --flags

    def build(self, plan):
        # add_stage returns a handle: a future for "whatever this stage produces"
        groceries = plan.add_stage(
            "buy_groceries",
            cmds=[f"echo {self.veggie} > {plan.dir('groceries.txt')}"],
            value=plan.dir("groceries.txt"),  # where its output will be
        )
        chopped = plan.add_stage(
            "chop_vegetables",
            deps=[groceries],  # waits for buy_groceries
            cmds=[f"echo chopped $(cat {groceries.value}) > {plan.dir('chopped.txt')}"],
            value=plan.dir("chopped.txt"),
        )
        cooked = plan.add_stage(
            "cook_vegetables",
            deps=[chopped],
            cmds=[f"echo cooked $(cat {chopped.value}) > {plan.dir('stew.txt')}"],
            value=plan.dir("stew.txt"),
        )
        plan.add_stage(  # a stage can be a Python function too
            "plate",
            deps=[cooked],
            fn=lambda: print(open(cooked.value).read()),
        )


if __name__ == "__main__":
    babydag.main(Dinner)
