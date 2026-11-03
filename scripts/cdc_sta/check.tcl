# CDC constraint check for afifo on an ORFS ASAP7 post-synthesis netlist (OpenSTA in OpenROAD). One mode per run.
# Environment: NETLIST, KIND (top: afifo_top harness with instance u_f and consumer register q; blk: afifo as top),
#   DEPTH, CONS (the extracted header commands, extract.py), MODE, TMPD (scratch directory), CLR=1 for
#   sta_preset_clear_arcs_enabled.
# MODE excl_none|excl_gray|excl_mem|excl_arst (top only): false-path the three categories the header names (Gray
#   endpoints, mem startpoints, the arst_n net), leaving one out for the negative controls, and report the
#   wr_clk <-> rd_clk endpoints that are left. excl_none must leave none; each control must bring back its category.
# MODE text: the header commands as written; every crossing endpoint must be in the path-delay group for max and min.
# MODE post (top only): the same checks on the ORFS result after floorplan and CTS (ODB, SDC: 4_cts.odb / 4_cts.sdc, the
#   SDC as the flow wrote it back, propagated clocks), plus: no crossing endpoint violated, buffers on the synchronizer
#   nets (EXPECT_HB 0: none; 1: some, for the controls), EXPECT_VIOL (0, or some for the clocks-only control), dont_touch flags in the database (EXPECT_DT nets|nets+inst|none).
#   With POST_BASIC set (controls without the header SDC) only the last two are checked.
# Each check prints "RESULT <name> PASS|FAIL <detail>".
set P $::env(FLOW_HOME)/platforms/asap7
read_lef $P/lef/asap7_tech_1x_201209.lef
read_lef $P/lef/asap7sc7p5t_28_R_1x_220121a.lef
foreach l {SEQ_RVT_TT_nldm_220123.lib INVBUF_RVT_TT_nldm_220122.lib.gz AO_RVT_TT_nldm_211120.lib.gz
           OA_RVT_TT_nldm_211120.lib.gz SIMPLE_RVT_TT_nldm_211120.lib.gz} { read_liberty $P/lib/NLDM/asap7sc7p5t_$l }
set KIND $::env(KIND); set MODE $::env(MODE)
if {$MODE eq "post"} {
  read_db $::env(ODB)
  read_sdc $::env(SDC)
} else {
  read_verilog $::env(NETLIST)
  link_design [expr {$KIND eq "top" ? "afifo_top" : "afifo"}]
}
if {[info exists ::env(CLR)]} { set ::sta_preset_clear_arcs_enabled 1 }
if {$MODE ne "post"} {
  create_clock -name wr_clk -period 667 [get_ports wr_clk]
  create_clock -name rd_clk -period 833 [get_ports rd_clk]
}
if {$MODE eq "post"} {
  set HS {u_f\.}
} elseif {$KIND eq "top"} {
  set HS {u_f\.}
  set_input_delay 0 -clock wr_clk [get_ports {wr_rst_n wr_valid_i wr_data_i*}]
  set_input_delay 0 -clock rd_clk [get_ports {rd_rst_n rd_ready_i}]
} else {
  set HS {}
  set_input_delay 0 -clock wr_clk [get_ports {wr_rst_n wr_valid wr_data*}]
  set_input_delay 0 -clock rd_clk [get_ports {rd_rst_n rd_ready}]
}

set DEPTH $::env(DEPTH)
source [file join [file dirname [info script]] sel.tcl]
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

if {$KIND eq "top" && [string match excl_* $MODE]} {
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

# MODE text / post
if {$MODE eq "post" && [info exists ::env(POST_BASIC)]} {
  set PFX post   ;# controls: only the buffer and dont_touch checks below
} else {
set PFX [expr {$MODE eq "post" ? "post" : "text"}]
result msb_merge [expr {[llength $wg] == $AW && [llength $rg] == $AW && [llength $WR1D] == $AW + 1 && [llength $RW1D] == $AW + 1}] \
  "wgray flops [llength $wg], rgray flops [llength $rg], wgray_r1 D [llength $WR1D], rgray_w1 D [llength $RW1D] (AW $AW: header says AW Gray flops, AW+1 synchronizer bits)"
if {$MODE eq "text"} { source $::env(CONS) }
foreach dly {max min} {
  set kind [expr {$dly eq "max" ? "max_delay/setup:path delay" : "min_delay/hold:path delay"}]
  set d [groups -from [get_clocks wr_clk] -to [get_clocks rd_clk] -path_delay $dly]
  set n [expr {[dict exists $d $kind] ? [llength [dict get $d $kind]] : 0}]
  set exp [expr {[llength $WR1D] + $ncons}]
  result "${PFX}_wr_rd_$dly" [expr {[dict size $d] == 1 && $n == $exp}] "groups {[dict keys $d]}, path delay $n (expect $exp: [llength $WR1D] wgray_r1 + $ncons consumer)"
  set d [groups -from [get_clocks rd_clk] -to [get_clocks wr_clk] -path_delay $dly]
  set n [expr {[dict exists $d $kind] ? [llength [dict get $d $kind]] : 0}]
  result "${PFX}_rd_wr_$dly" [expr {[dict size $d] == 1 && $n == [llength $RW1D]}] "groups {[dict keys $d]}, path delay $n (expect [llength $RW1D] rgray_w1)"
}
set n 0; foreach c $sync { incr n [llength [all_eps [groups -to [PinsNamed $c RESETN] -path_delay min_max]]] }
result ${PFX}_sync_recovery [expr {[llength $sync] == 4 && $n == 0}] "$n recovery/removal checks left at the 4 synchronizer flops (expect 0)"
set n [llength [all_eps [groups -from [FF "^${HS}wrst_n\\\$"] -to [PinsNamed $wbin RESETN] -path_delay min_max]]]
result ${PFX}_wrst_wbin [expr {$n > 0}] "wrst_n -> wbin RESETN checks $n (expect > 0: same-clock recovery/removal kept)"
set to [expr {$KIND eq "top" ? $cons : [get_ports rd_data*]}]
set d [groups -from $rbin -to $to -path_delay max]
result ${PFX}_rbin_cons [expr {[dict keys $d] eq "max_delay/setup:rd_clk"}] "rbin -> consumer groups {[dict keys $d]} (expect an ordinary rd_clk path)"
}
if {$KIND eq "blk"} {
  # block level: the min output delay must make the rbin -> rd_data hold check a real one (required time > 0 here)
  set fn $::env(TMPD)/req_[pid].txt
  report_checks -from $rbin -to [get_ports rd_data*] -path_delay min -format full -digits 1 > $fn
  set f [open $fn]; set t [read $f]; close $f; file delete $fn
  set req ""; regexp {([-0-9.]+)\s+data required time} $t -> req
  result blk_rbin_min_required [expr {$req ne "" && $req > 0}] "rbin -> rd_data hold: data required time $req (expect > 0; -300 with a plain set_output_delay 300)"
}
if {$MODE eq "post"} {
  # crossing endpoints violated, max and min, with the clock propagated (EXPECT_VIOL 0: none; some: the related-clock
  # setup violations of the clocks-only control, max only)
  foreach dly {max min} {
    set n 0
    foreach {a b} {wr_clk rd_clk rd_clk wr_clk} {
      set fn $::env(TMPD)/vio_[pid].txt
      report_checks -from [get_clocks $a] -to [get_clocks $b] -path_delay $dly -group_path_count 100000 -endpoint_path_count 1 -format end > $fn
      set f [open $fn]; set t [read $f]; close $f; file delete $fn
      incr n [regexp -all {VIOLATED} $t]
    }
    if {$::env(EXPECT_VIOL) eq "some"} {
      if {$dly eq "max"} { result post_viol_max [expr {$n > 0}] "$n violated crossing endpoints (expect some: clocks-only SDC, related-clock paths)" }
    } else {
      result post_viol_$dly [expr {$n == 0}] "$n violated crossing endpoints (expect 0)"
    }
  }
  # buffers on the synchronizer-internal nets
  set nb 0; set nets 0
  foreach n $SYNCN {
    incr nets
    foreach p [get_pins -of_objects $n] {
      set rn [get_property [get_cells -of_objects $p] ref_name]
      if {[regexp {^(HB|BUF)} $rn]} { incr nb }
    }
  }
  set eh $::env(EXPECT_HB)
  result post_sync_buffers [expr {$eh ? $nb > 0 : $nb == 0}] "$nb buffer pins on the $nets synchronizer nets (expect [expr {$eh ? {some} : 0}])"
  # dont_touch flags in the database
  proc norm {x} { return [string map {\\ {}} $x] }
  set blk [ord::get_db_block]
  array set dtn {}; array set dti {}
  foreach n [$blk getNets] { if {[$n isDoNotTouch]} { set dtn([norm [$n getName]]) 1 } }
  foreach i [$blk getInsts] { if {[$i isDoNotTouch]} { set dti([norm [$i getName]]) 1 } }
  set wantn [concat $SYNCN $ARST]; set missn 0
  foreach n $wantn { if {![info exists dtn([norm [get_full_name $n]])]} { incr missn } }
  set missi 0
  foreach c $SYNCFF { if {![info exists dti([norm [get_full_name $c]])]} { incr missi } }
  set ed $::env(EXPECT_DT)
  switch $ed {
    nets      { set ok [expr {$missn == 0 && $missi == [llength $SYNCFF]}] }
    nets+inst { set ok [expr {$missn == 0 && $missi == 0}] }
    none      { set ok [expr {$missn == [llength $wantn] && $missi == [llength $SYNCFF]}] }
  }
  result post_dont_touch $ok "dont_touch missing on $missn of [llength $wantn] nets, $missi of [llength $SYNCFF] flops (expect $ed)"
}
exit
