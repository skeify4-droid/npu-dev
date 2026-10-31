create_clock -name clk -period 4.0 [get_ports clk]
set_input_delay 0.5 -clock clk [get_ports {a_in* b_in*}]
set_output_delay 0.5 -clock clk [get_ports y*]
