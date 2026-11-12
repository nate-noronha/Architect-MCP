---
id: hmc-electronics-high-voltage
title: Electronics Bench Soldering, LiPo & High-Voltage (>50V) Safety
severity: steward_signoff_required
priority: 85
always_include: false
applies_to_areas:
  - electronics-bench
applies_to_subsystems:
  - electrical
  - firmware
trigger_keywords:
  - voltage
  - mains
  - 120v
  - 240v
  - 48v
  - 60v
  - lipo
  - battery
  - solder
  - power supply
  - inverter
  - esc
required_ppe:
  - Safety glasses while soldering or trimming component leads
  - HEPA/activated-carbon fume extractor within 6 inches of solder joint
  - Fireproof LiPo charging bag for all lithium-polymer packs
requires_steward_signoff: true
---
# Electronics & High-Energy Storage Rules

1. **Steward Sign-Off (>50V DC or Mains AC)**: Any circuit exceeding **50V DC** or connecting to **120V AC mains** requires a Shop Steward wiring & strain-relief inspection before energizing.
2. **LiPo Battery Handling**: Charge LiPo packs exclusively inside steel/fiberglass containment bags on the non-flammable stone surface; never exceed 1C unless pack rating is verified.
3. **Lead-Free Soldering & Lead Wash**: Use fume extractors on all stations and wash hands after handling solder or brass tip cleaners.
4. **Current-Limited Bring-Up**: Power new PCBs for the first time on a current-limited bench supply before connecting batteries.
