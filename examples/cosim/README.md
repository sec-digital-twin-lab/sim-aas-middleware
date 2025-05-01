# Co-Simulation Example: Room and Thermostat Controllers

This example demonstrates a **synchronized co-simulation** between two processors - 
a `Room` model and a `Thermostat` controller - coordinated through direct socket 
communication, orchestrated by the Sim-aaS Middleware.

The example showcases how distributed models can interact in real-time to perform coupled 
simulation tasks, relying on synchronized message exchange over a network connection.

## Objective

Simulate the thermal dynamics of a room under thermostat control:
- The **Room** model tracks temperature based on heating and cooling rates.
- The **Thermostat** model observes the room's temperature and decides whether to 
turn the heater on or off based on configured thresholds.

This setup demonstrates how to:
- Perform **co-simulation** between coupled processors.
- Dynamically determine network addresses using the RTI.
- Exchange messages in real time via custom socket connections.
- Aggregate output from both sides of the interaction.

## Processors

### ThermostatProcessor
- Controls the heating system.
- Connects to the `RoomProcessor` via TCP.
- Based on temperature thresholds, sends commands:
  - `"HEATER_ON"` to raise temperature.
  - `"HEATER_OFF"` to stop heating.
  - `"NO_CHANGE"` to maintain current state.

### RoomProcessor
- Simulates room temperature evolution based on heater state.
- Listens for commands from the thermostat each timestep.
- Updates and returns a list of temperatures over time.

## Simulation Flow

1. The **RoomProcessor** starts a server socket on port `7001` and waits for a connection.
2. The **ThermostatProcessor**, once ready, retrieves the network address of the room job
from the RTI and connects to it.
3. Each simulation step:
   - The room sends its current temperature and timestep.
   - The thermostat reads the value and decides on the heater command.
   - The room applies the heating/cooling logic accordingly.
4. The loop continues for a predefined number of steps or until cancelled.

## Example Parameters

### RoomProcessor Input

```json
{
  "initial_temp": 15.0,
  "heating_rate": 0.5,
  "cooling_rate": -0.2,
  "max_steps": 50
}
```

### ThermostatProcessor Input
```json
{
  "threshold_low": 18.0,
  "threshold_high": 22.0
}
```