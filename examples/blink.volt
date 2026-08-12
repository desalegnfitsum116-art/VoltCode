// blink.volt — Blink the onboard LED
var led = DigitalPin.Init(13, DigitalPin.OUTPUT)
var board = Arduino.Init()

while true:
    led.write(1)
    Delay(1000)
    led.write(0)
    Delay(1000)
