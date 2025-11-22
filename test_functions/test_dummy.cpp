#include <iostream>
#include <vector>

// A simple function
int add(int a, int b) {
    return a + b;
}

/*
 * Multi-line comment
 */
void greet(std::string name) {
    std::cout << "Hello, " << name << "!" << std::endl;
}

class Calculator {
public:
    int multiply(int a, int b) {
        return a * b;
    }
};
