// servo_sweep.volt — Sweep a servo back and forth
import Servo

var board = Arduino.Init()
var myServo = Servo.Init(9)
var pos int = 0

func sweep(start int, end int, step int):
    var angle = start
    while angle <= end:
        myServo.write(angle)
        Delay(15)
        angle = angle + step

while true:
    sweep(0, 180, 1)
    sweep(180, 0, -1)
