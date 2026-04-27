# EMS — Energy Management System

A brand-agnostic Home Assistant custom integration that intelligently manages solar panels, home batteries, and EV chargers using a configurable priority ladder.

## Features

- **Peak shaving** — discharge battery to stay within your grid import cap
- **Proactive grid charging** — top up the battery when electricity prices are cheap
- **EV smart scheduling** — charge your EV in the cheapest price window before your deadline
- **Export curtailment** — stop exporting when the feed-in price is zero or negative
- **Forecast-driven planning** — use PV and load forecasts to anticipate evening shortfalls

## Zero hard-coded entity IDs

All sensor mappings are done through a guided setup wizard. Works with any inverter brand (Solax, SolarEdge, Victron, Sungrow…) and any EV charger exposed as a select entity.

## Requirements

- Home Assistant 2024.11+
- Inverter exposed as a `select` entity (any brand)
- (Optional) EV charger as a `select` entity
- (Optional) Dynamic electricity price sensor
