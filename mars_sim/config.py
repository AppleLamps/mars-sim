"""Tunable simulation constants for the Mars habitat sim."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimConfig:
    # Dust storm (apply_phase_events, ~line 278)
    dust_storm_chance: float = 0.15
    dust_multiplier_min: float = 0.3
    dust_multiplier_max: float = 0.7
    dust_duration_min: int = 1
    dust_duration_max: int = 3

    # Passive drain per phase (apply_phase_events, ~line 299)
    o2_loss_min: float = 0.3
    o2_loss_max: float = 0.8
    water_loss_min: float = 2.0
    water_loss_max: float = 5.0
    food_loss_min: float = 0.2
    food_loss_max: float = 0.4
    integrity_loss_min: float = 0.2
    integrity_loss_max: float = 0.5

    # ISRU recovery (apply_phase_events, ~line 311)
    isru_power_floor: float = 30.0
    isru_ls_alloc_floor: float = 25.0
    isru_o2_base: float = 0.15
    isru_water_base: float = 0.5

    # Greenhouse passive food gain (apply_phase_events, ~line 320)
    greenhouse_food_factor: float = 0.1

    # Anomaly chance (apply_phase_events, ~line 329)
    anomaly_chance: float = 0.08

    # Repair effort -> boost and power cost (apply_action REPAIR_SYSTEM, ~line 385)
    repair_boost_low: float = 1.0
    repair_boost_medium: float = 2.5
    repair_boost_high: float = 5.0
    repair_power_low: float = 2.0
    repair_power_medium: float = 5.0
    repair_power_high: float = 10.0

    # EVA (apply_action CONDUCT_EVA, ~line 485)
    eva_fuel_per_hour: float = 3.5
    eva_power_per_hour: float = 1.5
    eva_sample_chance: float = 0.6
    eva_incident_chance: float = 0.15

    # Blocking thresholds (get_blocked_actions, ~line 216)
    rover_fuel_eva_floor: float = 10.0
    dust_eva_severity_floor: float = 0.5
