#!/bin/bash

# Print the terminal's 16 ANSI colors as labeled swatches.

names=(black red green yellow blue magenta cyan white)

for offset in 0 8; do
  for _ in 1 2; do
    for i in {0..7}; do printf '\e[48;5;%dm        \e[0m ' $((i + offset)); done
    echo
  done
  for i in {0..7}; do
    label=${names[i]}
    (( offset )) && label="br.$label"
    printf '%-8s ' "$label"
  done
  printf '\n\n'
done
