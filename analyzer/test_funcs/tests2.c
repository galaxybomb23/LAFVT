// leopard_tests.c

int f0(int x) {
    return x + 1;
}

int f_if(int x) {
    if (x > 0) x++;
    return x;
}

int f_if_else(int x) {
    if (x > 0) x++;
    else x--;
    return x;
}

int f_if_and(int a, int b) {
    if (a && b) return 1;
    return 0;
}

int f_nested_if(int x, int y) {
    if (x > 0) {
        if (y > 0) x++;
    }
    return x;
}

int f_loop(int n) {
    int s = 0;
    for (int i = 0; i < n; i++) s += i;
    return s;
}

int f_nested_loops(int n) {
    int s = 0;
    for (int i = 0; i < n; i++) {
        while (n > 0) {
            s++;
            break;
        }
    }
    return s;
}

int f_switch(int x) {
    switch (x) {
        case 0: return 0;
        case 1: return 1;
        case 2: return 2;
        default: return -1;
    }
}

int f_ternary(int x, int y) {
    return (x > y) ? x : y;
}

int f_ptr(int *p) {
    int x = *p;   // your code counts this as a "pointer op"
    p++;          // your code counts this as a "pointer op"
    return x + p[0]; // array subscript not counted by your pointer-op rules
}

int callee(int a) { return a; }

int f_call(int x) {
    return callee(x); // your V2 logic will count BOTH "callee" and "x"
}
