# linter_test.py - A test file with deliberate syntax errors

def calculate_sum(numbers)
    total = 0
    for num in numbers:
        total += num
    print("Sum:", total)
    return total

def greet(name):
    message = f"Hello, {name}!"
    print(message)


print(calculate_sum([1, 2, 3]))
greet("World")
