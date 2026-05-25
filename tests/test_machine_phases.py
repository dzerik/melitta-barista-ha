"""P2a — MachinePhase + GeneratedRecipe.machine_phases pydantic model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from custom_components.melitta_barista.panel_api import (
    GeneratedRecipe,
    MachinePhase,
    RecipeComponent,
    RecipeStep,
)


def test_machine_phase_minimal():
    phase = MachinePhase(component=RecipeComponent())
    assert isinstance(phase.component, RecipeComponent)
    assert phase.user_action_before == []


def test_machine_phase_with_user_action():
    phase = MachinePhase(
        component=RecipeComponent(process="coffee", portion_ml=40),
        user_action_before=[
            RecipeStep(order=1, action="Take a cappuccino cup"),
        ],
    )
    assert len(phase.user_action_before) == 1
    assert phase.user_action_before[0].action == "Take a cappuccino cup"


def test_generated_recipe_requires_machine_phases():
    with pytest.raises(ValidationError):
        GeneratedRecipe(
            name="Test",
            machine_phases=[],
            steps=[RecipeStep(order=1, action="do")],
        )


def test_generated_recipe_max_two_phases():
    with pytest.raises(ValidationError):
        GeneratedRecipe(
            name="Test",
            machine_phases=[
                MachinePhase(component=RecipeComponent()),
                MachinePhase(component=RecipeComponent()),
                MachinePhase(component=RecipeComponent()),
            ],
            steps=[RecipeStep(order=1, action="do")],
        )


def test_generated_recipe_single_phase():
    recipe = GeneratedRecipe(
        name="Single Espresso",
        machine_phases=[
            MachinePhase(component=RecipeComponent(process="coffee", portion_ml=40)),
        ],
        steps=[RecipeStep(order=1, action="brew")],
    )
    assert len(recipe.machine_phases) == 1
    assert recipe.machine_phases[0].component.portion_ml == 40


def test_generated_recipe_two_phases_with_user_action():
    recipe = GeneratedRecipe(
        name="Layered Latte",
        machine_phases=[
            MachinePhase(component=RecipeComponent(process="coffee", portion_ml=40)),
            MachinePhase(
                component=RecipeComponent(process="milk", portion_ml=120),
                user_action_before=[
                    RecipeStep(order=1, action="Stir gently before second pour"),
                ],
            ),
        ],
        steps=[RecipeStep(order=1, action="prepare")],
    )
    assert len(recipe.machine_phases) == 2
    assert len(recipe.machine_phases[1].user_action_before) == 1


def test_generated_recipe_no_longer_has_component1_or_component2():
    assert "machine_phases" in GeneratedRecipe.model_fields
    assert "component1" not in GeneratedRecipe.model_fields
    assert "component2" not in GeneratedRecipe.model_fields
