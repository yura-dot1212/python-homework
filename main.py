a = float(input("Введіть число a: "))
b = float(input("Введіть число b: "))

action = input("Введіть дію (+, -, *, /): ")

if action == "+":
    print(a + b)
elif action == "-":
    print(a - b)
elif action == "*":
    print(a * b)
elif action == "/":
    if b == 0:
        print("Ділення на нуль")
    else:
        print(a / b)
else:
    print("Невідома дія")
