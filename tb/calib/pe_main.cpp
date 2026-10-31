// Verilator calibration driver: clocks a pe_array for a fixed number of cycles.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include "VTOP.h"
#include "verilated.h"
template <class T> static void fill(T& v, uint32_t x) { v = (T)x; }
template <size_t K> static void fill(VlWide<K>& v, uint32_t x) {
  for (size_t w = 0; w < K; w++) v[w] = x + 0x9e3779b9u * (uint32_t)w;
}
int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  VTOP* d = new VTOP;
  d->rst_n = 0; d->clk = 0; d->eval();
  for (int i = 0; i < 4; i++) { d->clk = 1; d->eval(); d->clk = 0; d->eval(); }
  d->rst_n = 1;
  const int cycles = 20000;
  uint32_t x = 12345;
  for (int c = 0; c < cycles; c++) {
    x = x * 1664525u + 1013904223u;
    fill(d->a_in, x); fill(d->b_in, x ^ 0x5a5a5a5au);
    d->clk = 1; d->eval(); d->clk = 0; d->eval();
  }
  printf("done %d cycles\n", cycles);
  delete d; return 0;
}
