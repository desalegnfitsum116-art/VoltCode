// serial_echo.volt — Reflect serial input over the UART.
// Echoes each received character and toggles the built-in LED.

var board = Arduino.Init()
var led = DigitalPin.Init(13, DigitalPin.OUTPUT)

Serial.begin(9600)
Serial.println("Volt serial echo ready")

while true:
    var ch int = Serial.read()
    if ch >= 0:
        led.write(1)
        Serial.write(ch)
    else:
        led.write(0)
        Delay(50)