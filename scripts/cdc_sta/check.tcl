# CDC constraint check for afifo on an ORFS ASAP7 post-synthesis netlist (OpenSTA in OpenROAD). One mode per run.
# Environment: NETLIST, KIND (top: afifo_top harness with instance u_f and consumer register q; blk: afifo as top),
#   DEPTH, WIDTH, CONS (the extracted header commands, extract.py), MODE, TMPD (scratch directory), CLR=1 for
#   sta_preset_clear_arcs_enabled. params.tcl (extract.py, next to this file) holds the numbers of the header commands.
#   post only: LIBS (the flow's liberty files: make print-LIB_FILES, CORNER from the platform config) and SETRC (the
#   platform's setRC.tcl), STAGE (4_cts, 5_1_grt, 5_route), RCX (the platform's RCX_RULES, 5_route); the design is timed at
#   the flow's corner with the parasitics of the stage: estimate_parasitics -placement (4_cts), -global_routing (5_1_grt),
#   RCX extraction read back from a SPEF (5_route).
# MODE excl_none|excl_gray|excl_mem|excl_arst (top only): false-path the three categories the header names (Gray
#   endpoints, mem startpoints, the arst_n net), leaving one out for the negative controls, and report the
#   wr_clk <-> rd_clk endpoints that are left. excl_none must leave none; each control must bring back its category.
# MODE text: the header commands as written; every crossing endpoint must be in the path-delay group for max and min.
#   Both modes also check the values: the max delays of the Gray endpoints (T_wr-U_g, T_rd-U_g) and of the mem paths
#   (2*T_rd-U_m) as the report prints them, the min delays (0) and the library setup / hold term (or the block-level
#   output delay) that makes up the required time.
# MODE post (top only): the same checks on the ORFS result (ODB, SDC: <stage>.odb / <stage>.sdc, the SDC as the flow wrote
#   it back, propagated clocks; 4_cts, and 5_1_grt / 5_route when the flow ran through routing), plus: no crossing
#   endpoint violated (and every crossing endpoint reported), hold slack >= 0 at the D pins of the synchronizer second
#   stages, buffers on the synchronizer nets (EXPECT_HB 0: none; 1: some, for the controls), EXPECT_VIOL (0, or some for
#   the clocks-only control), dont_touch flags in the database (EXPECT_DT nets|nets+inst|none).
#   With POST_BASIC set (controls without the header SDC) only the last four are checked.
# Each check prints "RESULT <name> PASS|FAIL <detail>".
set P $::env(FLOW_HOME)/platforms/asap7
read_lef $P/lef/asap7_tech_1x_201209.lef
read_lef $P/lef/asap7sc7p5t_28_R_1x_220121a.lef
set KIND $::env(KIND); set MODE $::env(MODE)
if {$MODE eq "post"} {
  # the flow's own corner and libraries (inner.sh takes them from the platform config), not a fixed list
  if {![info exists ::env(LIBS)] || [llength $::env(LIBS)] == 0 || ![info exists ::env(SETRC)]} {
    puts "RESULT post_env FAIL LIBS / SETRC not set"; exit
  }
  foreach l $::env(LIBS) { read_liberty $l }
} else {
  foreach l {SEQ_RVT_TT_nldm_220123.lib INVBUF_RVT_TT_nldm_220122.lib.gz AO_RVT_TT_nldm_211120.lib.gz
             OA_RVT_TT_nldm_211120.lib.gz SIMPLE_RVT_TT_nldm_211120.lib.gz} { read_liberty $P/lib/NLDM/asap7sc7p5t_$l }
}
if {$MODE eq "post"} {
  read_db $::env(ODB)
  read_sdc $::env(SDC)
  source $::env(SETRC)
  # parasitics as the flow has them at that stage: placement estimate after CTS (the flow's cts.tcl), the global-route
  # estimate after global route (global_route.tcl), RCX extraction of the routed wires after detail route (final_outputs.tcl:
  # define_process_corner, set_extraction_rules_file $RCX_RULES, extract_parasitics, write_spef, read_spef)
  switch $::env(STAGE) {
    4_cts    { estimate_parasitics -placement; set PARA placement }
    5_1_grt  { estimate_parasitics -global_routing; set PARA global_routing }
    5_route  {
      if {![info exists ::env(RCX)]} { puts "RESULT post_env FAIL RCX not set"; exit }
      define_process_corner -ext_model_index 0 X
      set_extraction_rules_file $::env(RCX)
      extract_parasitics
      write_spef $::env(TMPD)/post_[pid].spef
      read_spef $::env(TMPD)/post_[pid].spef
      file delete $::env(TMPD)/post_[pid].spef
      set PARA rcx
    }
    default  { puts "RESULT post_env FAIL unknown STAGE $::env(STAGE)"; exit }
  }
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

set DEPTH $::env(DEPTH); set WIDTH $::env(WIDTH)
source [file join [file dirname [info script]] sel.tcl]
source [file join [file dirname [info script]] params.tcl]
if {$KIND eq "top"} { set cons [FF {^q(\[|\$)}]; set ncons [llength $cons] } else { set ncons [llength [get_ports rd_data*]] }
puts "SETS mode=$MODE clr=$::sta_preset_clear_arcs_enabled AW=$AW wg=[llength $wg] wg_r1=[llength $wg_r1] rg=[llength $rg] rg_w1=[llength $rg_w1] mem=[llength $mem] cons=$ncons sync=[llength $sync] S1=[llength $S1] S2=[llength $S2] syncn=[llength $SYNCN] syncff=[llength $SYNCFF]"

proc result {name ok detail} { puts "RESULT $name [expr {$ok ? "PASS" : "FAIL"}] $detail" }
# An empty or short selection makes the exceptions and checks below vacuous (OpenSTA drops an exception on an empty
# selection with only a warning, STA-0472; a check over an empty set passes), so every set has its expected size first,
# as a function of AW (and WIDTH, DEPTH). Synchronizer pairs: AW+1 wgray bits, AW+1 rgray bits, wrst_n, rrst_n. The ASAP7
# flops have a QN output only, so an inverter (or, in the ctsn control, a hold buffer) sits between the two stages of
# every pair and the nets are two per pair: the first stage's output net and the second stage's D net. Gray source flops:
# AW wgray (the MSB is merged into wbin[AW]) + wbin[AW], AW rgray + rbin[AW].
set nap [expr {$ARST eq "" ? 0 : [llength [get_pins -of_objects $ARST]]}]
set npair [expr {2 * $AW + 4}]
set want [list \
  arst_pins $nap 5   wgray [llength $wg] $AW   rgray [llength $rg] $AW \
  wgray_r1 [llength $wg_r1] [expr {$AW + 1}]   rgray_w1 [llength $rg_w1] [expr {$AW + 1}] \
  WR1D [llength $WR1D] [expr {$AW + 1}]   RW1D [llength $RW1D] [expr {$AW + 1}] \
  mem [llength $mem] [expr {$DEPTH * $WIDTH}]   consumer $ncons $WIDTH   sync [llength $sync] 4 \
  S1 [llength $S1] $npair   S2 [llength $S2] $npair   SYNCN [llength $SYNCN] [expr {2 * $npair}] \
  SYNCFF [llength $SYNCFF] [expr {2 * $npair + 2 * $AW + 2}]]
set bad {}
foreach {n got exp} $want { if {$got != $exp} { lappend bad "$n $got (expect $exp)" } }
if {[llength $bad] > 0} { result "sets_$MODE" 0 "[join $bad {, }]"; exit }
result "sets_$MODE" 1 "AW $AW: [expr {[llength $want] / 3}] set sizes as expected (S1 = S2 = $npair, syncn [llength $SYNCN], syncff [llength $SYNCFF])"

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
# Full reports parsed into one record per path: {startpoint endpoint bound adjustments required}. bound is the number on the
# max_delay / min_delay line (empty if the path has none: the exception is not applied), adjustments the (description
# value) terms between that line and "data required time" (library setup / hold time, output external delay).
proc fullpaths {args} {
  set fn $::env(TMPD)/full_[pid]_[incr ::K].txt
  report_checks {*}$args -group_path_count 100000 -endpoint_path_count 1 -format full -digits 1 > $fn
  set f [open $fn]; set t [read $f]; close $f; file delete $fn
  set out {}; set st ""; set en ""; set bound ""; set adj {}; set in 0
  foreach l [split $t "\n"] {
    if {[regexp {^Startpoint: (\S+)} $l -> x]} { set st $x; set en ""; set bound ""; set adj {}; set in 0; continue }
    if {[regexp {^Endpoint: (\S+)} $l -> x]} { set en $x; continue }
    if {[regexp {^\s*(-?[0-9.]+)\s+(-?[0-9.]+)\s+(max|min)_delay\s*$} $l -> d c k]} { set bound $d; set in 1; continue }
    if {[regexp {^\s*(-?[0-9.]+)\s+data required time} $l -> r] && $st ne ""} {
      lappend out [list $st $en $bound $adj $r]; set st ""; set in 0; continue
    }
    if {$in && [regexp {^\s*(-?[0-9.]+)\s+(-?[0-9.]+)\s+(\S.*\S)\s*$} $l -> d c desc]} { lappend adj [list $desc $d] }
  }
  return $out
}
# The delay values: every path of a class must carry the bound the header gives, and nothing else in its required time but
# the capture flop's library setup / hold (or, at block level, the output delay). cls: gray_wr, gray_rd, cons.
proc check_values {pfx recs cls n exp dly} {
  set bad 0; set seen {}
  foreach r $recs {
    lassign $r st en bound adj req
    set want [list "library [expr {$dly eq "max" ? "setup" : "hold"}] time"]
    set tot 0.0
    if {$cls eq "cons" && $::KIND eq "blk"} {
      set want [list "output external delay"]
      set ev [expr {$dly eq "max" ? -$::SHARE : -$::CMIN}]
    }
    set okb [expr {$bound ne "" && abs($bound - $exp) < 0.05}]
    set oka [expr {[llength $adj] == 1 && [lindex $adj 0 0] eq [lindex $want 0]}]
    if {$oka} {
      set d [lindex $adj 0 1]; set tot $d
      if {[info exists ev] && abs($d - $ev) > 0.05} { set oka 0 }
    }
    set okr [expr {$bound ne "" && $oka && abs($req - ($bound + $tot)) < 0.15}]
    if {!($okb && $oka && $okr)} { incr bad }
    lappend seen "$bound/[join [lmap a $adj {lindex $a 1}] +]"
  }
  set vals [lsort -unique $seen]
  result ${pfx}_val_${cls}_$dly [expr {[llength $recs] == $n && $bad == 0}] "[llength $recs] paths (expect $n), bound/adjustment {[lrange $vals 0 3]} (expect $exp / [expr {$cls eq "cons" && $::KIND eq "blk" ? "output delay" : "library term"}]), $bad off"
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
foreach dly {max min} {
  set bg [expr {$dly eq "max" ? $TWR - $UG : 0}]; set br [expr {$dly eq "max" ? $TRD - $UG : 0}]
  set bm [expr {$dly eq "max" ? 2 * $TRD - $UM : 0}]
  set wr [fullpaths -from [get_clocks wr_clk] -to [get_clocks rd_clk] -path_delay $dly]
  set rw [fullpaths -from [get_clocks rd_clk] -to [get_clocks wr_clk] -path_delay $dly]
  set g {}; set m {}
  foreach r $wr { if {[regexp {wgray_r1\[} [lindex $r 1]]} { lappend g $r } else { lappend m $r } }
  check_values $PFX $g gray_wr [llength $WR1D] $bg $dly
  check_values $PFX $rw gray_rd [llength $RW1D] $br $dly
  check_values $PFX $m cons $ncons $bm $dly
}
set n 0; foreach c $sync { incr n [llength [all_eps [groups -to [PinsNamed $c RESETN] -path_delay min_max]]] }
result ${PFX}_sync_recovery [expr {[llength $sync] == 4 && $n == 0}] "$n recovery/removal checks left at the 4 synchronizer flops (expect 0)"
# Same-clock recovery / removal from each domain reset to every pointer set the header says stays timed: wrst_n to
# {wbin, wgray, rgray_w1, rgray_w2}, rrst_n to {rbin, rgray, wgray_r1, wgray_r2}. One result per set; every RESETN pin of the
# set needs its recovery check (max) and its removal check (min), an exact count (a false path on part of a set, or on
# the whole set, takes pins away), and all of them in the asynchronous path group.
foreach {src sname sset nexp} [list \
    wrst_n wbin $wbin [expr {$AW + 1}]   wrst_n wgray $wg $AW   wrst_n rgray_w1 $rg_w1 [expr {$AW + 1}]   wrst_n rgray_w2 $rg_w2 [expr {$AW + 1}] \
    rrst_n rbin $rbin [expr {$AW + 1}]   rrst_n rgray $rg $AW   rrst_n wgray_r1 $wg_r1 [expr {$AW + 1}]   rrst_n wgray_r2 $wg_r2 [expr {$AW + 1}]] {
  set pins [PinsNamed $sset RESETN]; set from [FF "^${HS}${src}\\\$"]
  set cnt {}; set okg 1
  foreach dly {max min} {
    set d [groups -from $from -to $pins -path_delay $dly]
    lappend cnt [llength [all_eps $d]]
    foreach k [dict keys $d] { if {![string match *:asynchronous $k]} { set okg 0 } }
  }
  result ${PFX}_rst_${src}_${sname} [expr {[llength $from] == 1 && [llength $pins] == $nexp && $cnt eq [list $nexp $nexp] && $okg}] \
    "$src -> $sname RESETN: [llength $pins] pins (expect $nexp), recovery / removal checks [lindex $cnt 0] / [lindex $cnt 1] (expect $nexp / $nexp), asynchronous group only: $okg"
}
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
  # setup violations of the clocks-only control, max only). An empty report is no pass: every crossing endpoint has to
  # be in it (WR1D + consumer for wr -> rd, RW1D for rd -> wr; the clocks-only control only has to have some).
  foreach dly {max min} {
    set n 0; set tot 0
    foreach {a b} {wr_clk rd_clk rd_clk wr_clk} {
      set fn $::env(TMPD)/vio_[pid].txt
      report_checks -from [get_clocks $a] -to [get_clocks $b] -path_delay $dly -group_path_count 100000 -endpoint_path_count 1 -format end > $fn
      set f [open $fn]; set t [read $f]; close $f; file delete $fn
      incr n [regexp -all {\(VIOLATED\)} $t]; incr tot [regexp -all {\((MET|VIOLATED)\)} $t]
    }
    set expn [expr {[llength $WR1D] + $ncons + [llength $RW1D]}]
    if {$::env(EXPECT_VIOL) eq "some"} {
      if {$dly eq "max"} { result post_viol_max [expr {$n > 0 && $tot > 0}] "$n of $tot reported crossing endpoints violated (expect some: clocks-only SDC, related-clock paths)" }
    } else {
      result post_viol_$dly [expr {$n == 0 && $tot == $expn}] "$n of $tot reported crossing endpoints violated (expect 0 of $expn)"
    }
  }
  # hold slack at the D pins of the synchronizer second stages (wgray_r2, rgray_w2, wrst_n, rrst_n), at the flow's corner
  # with the stage's parasitics. Their nets are dont_touch, so repair_timing cannot fix a hold violation there; this is the
  # only place it shows. The flow's hold margin (HOLD_SLACK_MARGIN) is not required, only slack >= 0.
  set mn 1e9; set worst ""; set np 0; set all {}
  foreach c $S2 {
    foreach path [find_timing_paths -to [PinsNamed $c D] -path_delay min] {
      incr np
      set sl [get_property $path slack]
      if {$sl < $mn} { set mn $sl; set worst [get_full_name $c] }
      lappend all "[regsub {\$_DFF.*} [regsub {^u_f\.} [get_full_name $c] {}] {}]=[format %.1f $sl]"
    }
  }
  result post_s2_hold [expr {$np == [llength $S2] && $mn >= 0}] "$PARA parasitics: worst hold slack [format %.1f $mn] ps at $worst over $np of [llength $S2] second-stage D pins (expect all, >= 0); every pin: [join $all { }]"
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
