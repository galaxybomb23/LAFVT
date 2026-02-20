#include <stdio.h>

// int t1(int a, int b) {
//     int c = a + b;
//     return c;
// }

int f(int x) {
  x = 3;
  return x;
}
int t2(int x) {
  if (x > 0) {
    if (x > 5) {
      x = x + 5;
    }
    if (x > 8) {
      x = 10;
    }
    x = x + 1;
  }
  return x;
}

//
//
// int t3(int x) {
//   if (x > 0) {
//     x++;
//   } else {
//     x--;
//   }
//   return x;
// }
//
//  int t4(int a, int b) {
//     if (a > 0 && b > 0) {
//         return 1;
//     }
//     return 0;
// }
//
int t5(int n) {
  int sum = 0;
  for (int i = 0; i < n; i++) {
    sum += i;
  }
  return sum;
}
//
//  int t6(int n) {
//     int c = 0;
//     for (int i = 0; i < n; i++) {
//         for (int j = 0; j < n; j++) {
//             c += i + j;
//         }
//     }
//     return c;
// }
//
//
//  int sink(int x) { return x; }
//
//  int t7(int a, int b) {
//     int z = a + b;
//     return sink(z);
// }
//
//  int t8(int *p) {
//     p++;
//     return 0;
// }
//
//  int t9(int *p) {
//     int *q = p + 1;
//     return (int)(q - p);
// }
int main() {
  int x = 4;
  int y = t3(x);
  printf("result of t2(4) = %d\n", y);
  return 0;
}
