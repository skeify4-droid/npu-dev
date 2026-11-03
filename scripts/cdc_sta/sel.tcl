# Selections of the afifo objects the header commands name, shared by check.tcl and the ORFS hooks (gen.py).
# Needs the Tcl variables HS (regexp prefix of the afifo instance name: {u_f\.} in the harness, {} for the block as top)
# and DEPTH. These are what the header's "Names" item says: flops by cell type (DFF*), never by instance name alone (after
# floorplan the tie cells are named after flops, u_f.wgray_r1[0]$_DFF_PN0__26), the mem registers by their instance prefix.
# Sets: WR1D / RW1D (D pins of wgray_r1 / rgray_w1), mem, ARST (arst_n net), SYNCN (synchronizer-internal nets: the output
# net of every first-stage flop and the D net of every second-stage flop, one net or two when synthesis put an inverter
# between), SYNCFF (synchronizer flops and Gray source flops), plus the flop sets used by check.tcl.
proc FF {re} {
  set l {}
  foreach c [get_cells *] {
    if {[regexp $re [get_full_name $c]] && [string match DFF* [get_property $c ref_name]]} { lappend l $c }
  }
  return $l
}
proc PinsNamed {cells lps} {
  set o {}
  foreach c $cells { foreach p [get_pins -of_objects $c] { if {[get_property $p lib_pin_name] in $lps} { lappend o $p } } }
  return $o
}
set AW 0; while {(1 << $AW) < $DEPTH} { incr AW }
set NS [string map {\\ {}} $HS]
set wg_r1 [FF "^${HS}wgray_r1\\\["]; set rg_w1 [FF "^${HS}rgray_w1\\\["]
set wg    [FF "^${HS}wgray\\\["];    set rg    [FF "^${HS}rgray\\\["]
set mem   [FF "^${HS}mem\\\["];      set rbin  [FF "^${HS}rbin\\\["];  set wbin [FF "^${HS}wbin\\\["]
set sync  [FF "^${HS}(wr_rs0|wrst_n|rd_rs0|rrst_n)\\\$"]
set ARST  [get_nets -quiet ${NS}arst_n]
set WR1D  [PinsNamed $wg_r1 D]; set RW1D [PinsNamed $rg_w1 D]
set S1 [concat $wg_r1 $rg_w1 [FF "^${HS}(wr_rs0|rd_rs0)\\\$"]]
set S2 [concat [FF "^${HS}(wgray_r2|rgray_w2)\\\["] [FF "^${HS}(wrst_n|rrst_n)\\\$"]]
set SYNCN {}; array set seen {}
foreach p [concat [PinsNamed $S1 {Q QN}] [PinsNamed $S2 D]] {
  set n [get_nets -of_objects $p]; set nm [get_full_name $n]
  if {![info exists seen($nm)]} { set seen($nm) 1; lappend SYNCN $n }
}
set SYNCFF [concat $S1 $S2 $wg [FF "^${HS}wbin\\\[$AW\\\]"] $rg [FF "^${HS}rbin\\\[$AW\\\]"]]
