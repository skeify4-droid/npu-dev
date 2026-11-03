# CDC constraint check for afifo on an ORFS ASAP7 post-synthesis netlist (OpenSTA in OpenROAD). One mode per run.
# Environment: NETLIST, KIND (top: afifo_top harness with instance u_f and consumer register q; blk: afifo as top),
#   DEPTH, CONS (the extracted header commands, extract.py), MODE, TMPD (scratch directory), CLR=1 for
#   sta_preset_clear_arcs_enabled.
# MODE excl_none|excl_gray|excl_mem|excl_arst (top only): false-path the three categories the header names (Gray
#   endpoints, mem startpoints, the arst_n net), leaving one out for the negative controls, and report the
#   wr_clk <-> rd_clk endpoints that are left. excl_none must leave none; each control must bring back its category.
# MODE text: the header commands as written; every crossing endpoint must be in the path-delay group for max and min.
# Each check prints "RESULT <name> PASS|FAIL <detail>".
set P $::env(FLOW_HOME)/platforms/asap7
read_lef $P/lef/asap7_tech_1x_201209.lef
read_lef $P/lef/asap7sc7p5t_28_R_1x_220121a.lef
foreach l {SEQ_RVT_TT_nldm_220123.lib INVBUF_RVT_TT_nldm_220122.lib.gz AO_RVT_TT_nldm_211120.lib.gz
           OA_RVT_TT_nldm_211120.lib.gz SIMPLE_RVT_TT_nldm_211120.lib.gz} { read_liberty $P/lib/NLDM/asap7sc7p5t_$l }
set KIND $::env(KIND); set MODE $::env(MODE)
read_verilog $::env(NETLIST)
link_design [expr {$KIND eq "top" ? "afifo_top" : "afifo"}]
if {[info exists ::env(CLR)]} { set ::sta_preset_clear_arcs_enabled 1 }
create_clock -name wr_clk -period 667 [get_ports wr_clk]
create_clock -name rd_clk -period 833 [get_ports rd_clk]
if {$KIND eq "top"} {
  set HS {u_f\.}
  set_input_delay 0 -clock wr_clk [get_ports {wr_rst_n wr_valid_i wr_data_i*}]
  set_input_delay 0 -clock rd_clk [get_ports {rd_rst_n rd_ready_i}]
} else {
  set HS {}
  set_input_delay 0 -clock wr_clk [get_ports {wr_rst_n wr_valid wr_data*}]
  set_input_delay 0 -clock rd_clk [get_ports {rd_rst_n rd_ready}]
}
set AW 0; while {(1 << $AW) < $::env(DEPTH)} { incr AW }

# flops by instance-name regexp (ASAP7 DFF cells only, so tie cells named after a flop are not picked up)
proc FF {re} {
  set l {}
  foreach c [get_cells *] {
    if {[regexp $re [get_full_name $c]] && [string match DFF* [get_property $c ref_name]]} { lappend l $c }
  }
  return $l
}
proc PinsNamed {cells lp} {
  set o {}
  foreach c $cells { foreach p [get_pins -of_objects $c] { if {[get_property $p lib_pin_name] eq $lp} { lappend o $p } } }
  return $o
}
set wg_r1 [FF "^${HS}wgray_r1\\\["]; set rg_w1 [FF "^${HS}rgray_w1\\\["]
set wg    [FF "^${HS}wgray\\\["];    set rg    [FF "^${HS}rgray\\\["]
set mem   [FF "^${HS}mem\\\["];      set rbin  [FF "^${HS}rbin\\\["];  set wbin [FF "^${HS}wbin\\\["]
set sync  [FF "^${HS}(wr_rs0|wrst_n|rd_rs0|rrst_n)\\\$"]
set ARST  [get_nets -quiet [expr {$KIND eq "top" ? "u_f.arst_n" : "arst_n"}]]
set WR1D  [PinsNamed $wg_r1 D]; set RW1D [PinsNamed $rg_w1 D]
if {$KIND eq "top"} { set cons [FF {^q(\[|\$)}]; set ncons [llength $cons] } else { set ncons [llength [get_ports rd_data*]] }
puts "SETS mode=$MODE clr=$::sta_preset_clear_arcs_enabled AW=$AW wg=[llength $wg] wg_r1=[llength $wg_r1] rg=[llength $rg] rg_w1=[llength $rg_w1] mem=[llength $mem] cons=$ncons sync=[llength $sync]"

proc result {name ok detail} { puts "RESULT $name [expr {$ok ? "PASS" : "FAIL"}] $detail" }
# an empty selection makes OpenSTA drop the exception with only a warning (STA-0472), so check the sets first
set nap [expr {$ARST eq "" ? 0 : [llength [get_pins -of_objects $ARST]]}]
if {$nap != 5 || [llength $WR1D] == 0 || [llength $RW1D] == 0 || [llength $mem] == 0 || [llength $sync] != 4 || $ncons == 0} {
  result "sets_$MODE" 0 "arst_n pins $nap (expect 5), wgray_r1 D [llength $WR1D], rgray_w1 D [llength $RW1D], mem [llength $mem], sync [llength $sync], consumer $ncons"
  exit
}

# endpoints of a report, per path group: returns a dict group -> list of endpoint names (instance or port part)
set ::K 0
proc groups {args} {
  set fn $::env(TMPD)/rep_[pid]_[incr ::K].txt
  report_checks {*}$args -group_path_count 100000 -endpoint_path_count 1 -format end > $fn
  set f [open $fn]; set t [read $f]; close $f; file delete $fn
  set d [dict create]; set cur ""
  foreach l [split $t "\n"] {
    if {[regexp {^(\S+) group (.+)$} $l -> kind g]} { set cur "$kind:$g"; dict set d $cur {}; continue }
    if {$cur ne "" && [regexp {^(\S+) .*\((MET|VIOLATED)\)} $l -> ep]} { dict lappend d $cur [lindex [split $ep /] 0] }
  }
  return $d
}
proc all_eps {d} { set o {}; dict for {g l} $d { lappend o {*}$l }; return $o }
proc count_re {l re} { set n 0; foreach e $l { if {[regexp $re $e]} { incr n } }; return $n }

if {$KIND eq "top" && $MODE ne "text"} {
  set drop [string range $MODE 5 end]
  if {$drop ne "gray"} { set_false_path -to $WR1D; set_false_path -to $RW1D }
  if {$drop ne "mem"}  { set_false_path -from $mem }
  if {$drop ne "arst"} { set_false_path -through $ARST }
  foreach dly {max min} {
    set wr [all_eps [groups -from [get_clocks wr_clk] -to [get_clocks rd_clk] -path_delay $dly]]
    set rw [all_eps [groups -from [get_clocks rd_clk] -to [get_clocks wr_clk] -path_delay $dly]]
    set det "wr->rd [llength $wr] rd->wr [llength $rw]"
    switch $drop {
      none { result "excl_none_$dly" [expr {[llength $wr] == 0 && [llength $rw] == 0}] "$det left (expect 0 0) [lrange $wr 0 3] [lrange $rw 0 3]" }
      gray { result "excl_gray_$dly" [expr {[llength $wr] == [llength $WR1D] && [count_re $wr {wgray_r1\[}] == [llength $wr]
                                            && [llength $rw] == [llength $RW1D] && [count_re $rw {rgray_w1\[}] == [llength $rw]}] "$det (expect [llength $WR1D] wgray_r1, [llength $RW1D] rgray_w1)" }
      mem  { result "excl_mem_$dly"  [expr {[llength $wr] == $ncons && [count_re $wr {^q(\[|\$)}] == $ncons && [llength $rw] == 0}] "$det (expect $ncons q, 0)" }
      arst { result "excl_arst_$dly" [expr {[count_re $wr {rd_rs0|rrst_n}] >= 2 && [count_re $rw {wr_rs0|wrst_n}] >= 2}] "$det (expect the synchronizer flops among them)" }
    }
  }
  exit
}

# MODE text
result msb_merge [expr {[llength $wg] == $AW && [llength $rg] == $AW && [llength $WR1D] == $AW + 1 && [llength $RW1D] == $AW + 1}] \
  "wgray flops [llength $wg], rgray flops [llength $rg], wgray_r1 D [llength $WR1D], rgray_w1 D [llength $RW1D] (AW $AW: header says AW Gray flops, AW+1 synchronizer bits)"
source $::env(CONS)
foreach dly {max min} {
  set kind [expr {$dly eq "max" ? "max_delay/setup:path delay" : "min_delay/hold:path delay"}]
  set d [groups -from [get_clocks wr_clk] -to [get_clocks rd_clk] -path_delay $dly]
  set n [expr {[dict exists $d $kind] ? [llength [dict get $d $kind]] : 0}]
  set exp [expr {[llength $WR1D] + $ncons}]
  result "text_wr_rd_$dly" [expr {[dict size $d] == 1 && $n == $exp}] "groups {[dict keys $d]}, path delay $n (expect $exp: [llength $WR1D] wgray_r1 + $ncons consumer)"
  set d [groups -from [get_clocks rd_clk] -to [get_clocks wr_clk] -path_delay $dly]
  set n [expr {[dict exists $d $kind] ? [llength [dict get $d $kind]] : 0}]
  result "text_rd_wr_$dly" [expr {[dict size $d] == 1 && $n == [llength $RW1D]}] "groups {[dict keys $d]}, path delay $n (expect [llength $RW1D] rgray_w1)"
}
set n 0; foreach c $sync { incr n [llength [all_eps [groups -to [PinsNamed $c RESETN] -path_delay min_max]]] }
result text_sync_recovery [expr {[llength $sync] == 4 && $n == 0}] "$n recovery/removal checks left at the 4 synchronizer flops (expect 0)"
set n [llength [all_eps [groups -from [FF "^${HS}wrst_n\\\$"] -to [PinsNamed $wbin RESETN] -path_delay min_max]]]
result text_wrst_wbin [expr {$n > 0}] "wrst_n -> wbin RESETN checks $n (expect > 0: same-clock recovery/removal kept)"
set to [expr {$KIND eq "top" ? $cons : [get_ports rd_data*]}]
set d [groups -from $rbin -to $to -path_delay max]
result text_rbin_cons [expr {[dict keys $d] eq "max_delay/setup:rd_clk"}] "rbin -> consumer groups {[dict keys $d]} (expect an ordinary rd_clk path)"
exit
