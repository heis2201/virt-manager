# Copyright (C) 2014 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import collections

from gi.repository import Gtk

import virtinst
from virtinst import log

from ..lib import uiutil
from ..baseclass import vmmGObjectUI


NetDev = collections.namedtuple('Netdev', ['name', 'is_bridge', 'slave_names'])


class vmmNetworkList(vmmGObjectUI):
    __gsignals__ = {
        "changed": (vmmGObjectUI.RUN_FIRST, None, []),
    }

    def __init__(self, conn, builder, topwin):
        vmmGObjectUI.__init__(self, "netlist.ui",
                              None, builder=builder, topwin=topwin)
        self.conn = conn

        self.builder.connect_signals({
            "on_net_source_changed": self._on_net_source_changed,
            "on_net_source_mode_changed": self._emit_changed,
            "on_net_portgroup_changed": self._emit_changed,
            "on_net_bridge_name_changed": self._emit_changed,
        })

        self._init_ui()
        self.top_label = self.widget("net-source-label")
        self.top_box = self.widget("net-source-box")

    def _cleanup(self):
        self.conn.disconnect_by_obj(self)
        self.conn = None

        self.top_label.destroy()
        self.top_box.destroy()


    ##########################
    # Initialization methods #
    ##########################

    def _init_ui(self):
        # [ network type, source name, label, sensitive?, net is active,
        #   manual bridge, net instance]
        model = Gtk.ListStore(str, str, str, bool, bool, bool, object)
        combo = self.widget("net-source")
        combo.set_model(model)

        text = Gtk.CellRendererText()
        combo.pack_start(text, True)
        combo.add_attribute(text, 'text', 2)
        combo.add_attribute(text, 'sensitive', 3)

        combo = self.widget("net-source-mode")
        # [xml value, label]
        model = Gtk.ListStore(str, str)
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        model.append(["bridge", _("Bridge")])
        model.append(["vepa", "VEPA"])
        model.append(["private", _("Private")])
        model.append(["passthrough", _("Passthrough")])
        combo.set_active(0)

        combo = self.widget("net-portgroup")
        # [xml value, label]
        model = Gtk.ListStore(str, str)
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        self.conn.connect("net-added", self._repopulate_network_list)
        self.conn.connect("net-removed", self._repopulate_network_list)
        self.conn.connect("interface-added", self._repopulate_network_list)
        self.conn.connect("interface-removed", self._repopulate_network_list)

    def _pretty_network_desc(self, nettype, source=None, netobj=None):
        if nettype == virtinst.DeviceInterface.TYPE_USER:
            return _("Usermode networking")

        extra = None
        if nettype == virtinst.DeviceInterface.TYPE_BRIDGE:
            ret = _("Bridge")
        elif nettype == virtinst.DeviceInterface.TYPE_VIRTUAL:
            ret = _("Virtual network")
            if netobj:
                extra = ": %s" % netobj.pretty_forward_mode()
        else:
            ret = nettype.capitalize()

        if source:
            ret += " '%s'" % source
        if extra:
            ret += " %s" % extra

        return ret

    def _build_source_row(self, nettype, source_name,
            label, is_sensitive, is_running, manual_bridge=False, key=None):
        return [nettype, source_name, label,
                is_sensitive, is_running, manual_bridge,
                key]

    def _find_virtual_networks(self):
        rows = []
        vnet_bridges = []
        default_label = None

        for net in self.conn.list_nets():
            nettype = virtinst.DeviceInterface.TYPE_VIRTUAL

            label = self._pretty_network_desc(nettype, net.get_name(), net)
            if not net.is_active():
                label += " (%s)" % _("Inactive")

            if net.get_xmlobj().virtualport_type == "openvswitch":
                label += " (OpenVSwitch)"

            if net.get_name() == "default":
                default_label = label

            rows.append(self._build_source_row(
                nettype, net.get_name(), label, True,
                net.is_active(), key=net.get_connkey()))

            # Build a list of vnet bridges, so we know not to list them
            # in the physical interface list
            vnet_bridge = net.get_bridge_device()
            if vnet_bridge:
                vnet_bridges.append(vnet_bridge)

        if not rows:
            label = _("No virtual networks available")
            rows.append(self._build_source_row(
                None, None, label, False, False))

        return rows, vnet_bridges, default_label

    def _find_physical_devices(self, vnet_bridges):
        rows = []
        can_default = False
        default_label = None
        skip_ifaces = ["lo"]

        vnet_taps = []
        for vm in self.conn.list_vms():
            for nic in vm.get_interface_devices_norefresh():
                if nic.target_dev and nic.target_dev not in vnet_taps:
                    vnet_taps.append(nic.target_dev)

        netdevs = {}
        for iface in self.conn.list_interfaces():
            name = iface.get_name()
            netdevs[name] = NetDev(name, iface.is_bridge(),
                                   iface.get_interface_names())
        for nodedev in self.conn.filter_nodedevs("net"):
            if nodedev.xmlobj.interface not in netdevs:
                netdev = NetDev(nodedev.xmlobj.interface, False, [])
                netdevs[nodedev.xmlobj.interface] = netdev

        # For every bridge used by a virtual network, and any slaves of
        # those devices, don't list them.
        for vnet_bridge in vnet_bridges:
            slave_names = netdevs.pop(vnet_bridge,
                                      NetDev(None, None, [])).slave_names
            for slave in slave_names:
                netdevs.pop(slave, None)

        for name, is_bridge, slave_names in list(netdevs.values()):
            if ((name in vnet_taps) or
                (name in [v + "-nic" for v in vnet_bridges]) or
                (name in skip_ifaces)):
                # Don't list this, as it is basically duplicating
                # virtual net info
                continue

            sensitive = True
            source_name = name

            label = _("Host device %s") % (name)
            if is_bridge:
                nettype = virtinst.DeviceInterface.TYPE_BRIDGE
                if slave_names:
                    extra = (_("Host device %s") % slave_names[0])
                    can_default = True
                else:
                    extra = _("Empty bridge")
                label = _("Bridge %s: %s") % (name, extra)

            elif self.conn.is_qemu() or self.conn.is_test():
                nettype = virtinst.DeviceInterface.TYPE_DIRECT
                label += (": %s" % _("macvtap"))

            else:
                nettype = None
                sensitive = False
                source_name = None
                label += (": %s" % _("Not bridged"))

            if can_default and not default_label:
                default_label = label

            rows.append(self._build_source_row(
                nettype, source_name, label, sensitive, True,
                key=name))

        return rows, default_label

    def _populate_network_model(self, model):
        model.clear()

        def _add_manual_bridge_row():
            manual_row = self._build_source_row(
                None, None, _("Specify shared device name"),
                True, False, manual_bridge=True)
            model.append(manual_row)

        if self.conn.is_qemu_session():
            nettype = virtinst.DeviceInterface.TYPE_USER
            r = self._build_source_row(
                nettype, None, self._pretty_network_desc(nettype), True, True)
            model.append(r)

            _add_manual_bridge_row()
            return

        (vnets, vnet_bridges, default_net) = self._find_virtual_networks()
        (iface_rows, default_bridge) = self._find_physical_devices(
            vnet_bridges)

        # Sorting is:
        # 1) Bridges
        # 2) Virtual networks
        # 3) direct/macvtap
        # 4) Disabled list entries
        # Each category sorted alphabetically
        bridges = [row for row in iface_rows if row[0] == "bridge"]
        direct = [row for row in iface_rows if row[0] == "direct"]
        disabled = [row for row in iface_rows if row[0] is None]

        for rows in [bridges, vnets, direct, disabled]:
            rows.sort(key=lambda r: r[2])
            for row in rows:
                model.append(row)

        # If there is a bridge device, default to that
        # If not, use 'default' network
        # If not present, use first list entry
        # If list empty, use no network devices
        label = default_bridge or default_net

        default = 0
        if not len(model):
            row = self._build_source_row(
                None, None, _("No networking"), True, False)
            model.insert(0, row)
            default = 0
        elif label:
            default = [idx for idx, model_label in enumerate(model) if
                       model_label[2] == label][0]

        _add_manual_bridge_row()
        return default

    def _check_network_is_running(self, net):
        # Make sure VirtualNetwork is running
        if not net.type == virtinst.DeviceInterface.TYPE_VIRTUAL:
            return
        devname = net.source

        netobj = None
        if net.type == virtinst.DeviceInterface.TYPE_VIRTUAL:
            for n in self.conn.list_nets():
                if n.get_name() == devname:
                    netobj = n
                    break

        if not netobj or netobj.is_active():
            return

        res = self.err.yes_no(_("Virtual Network is not active."),
            _("Virtual Network '%s' is not active. "
              "Would you like to start the network "
              "now?") % devname)
        if not res:
            return

        # Try to start the network
        try:
            netobj.start()
            log.debug("Started network '%s'", devname)
        except Exception as e:
            return self.err.show_err(_("Could not start virtual network "
                                  "'%s': %s") % (devname, str(e)))


    ###############
    # Public APIs #
    ###############

    def get_network_row(self):
        return uiutil.get_list_selected_row(self.widget("net-source"))

    def get_network_selection(self):
        bridge_entry = self.widget("net-bridge-name")
        row = self.get_network_row()
        if not row:
            return None, None, None, None

        net_type = row[0]
        net_src = row[1]
        net_check_bridge = row[5]

        if net_check_bridge and bridge_entry:
            net_type = virtinst.DeviceInterface.TYPE_BRIDGE
            net_src = bridge_entry.get_text() or None

        mode = None
        if self.widget("net-source-mode").is_visible():
            mode = uiutil.get_list_selection(self.widget("net-source-mode"))

        portgroup = None
        if self.widget("net-portgroup").is_visible():
            portgroup = uiutil.get_list_selection(self.widget("net-portgroup"))

        return net_type, net_src, mode, portgroup or None

    def build_device(self, macaddr, model=None):
        nettype, devname, mode, portgroup = self.get_network_selection()

        net = virtinst.DeviceInterface(self.conn.get_backend())
        net.type = nettype
        net.source = devname
        net.macaddr = macaddr
        net.model = model
        net.source_mode = mode
        net.portgroup = portgroup

        return net

    def validate_device(self, net):
        self._check_network_is_running(net)
        net.validate()

    def reset_state(self):
        self._repopulate_network_list()

        net_err = None
        if (not self.conn.support.conn_nodedev() or
            not self.conn.support.conn_interface()):
            net_err = _("Libvirt version does not support "
                        "physical interface listing.")

        net_warn = self.widget("net-source-warn")
        net_warn.set_visible(bool(net_err))
        net_warn.set_tooltip_text(net_err or "")

        self.widget("net-bridge-name").set_text("")
        self.widget("net-source-mode").set_active(0)
        self.widget("net-portgroup").get_child().set_text("")

    def set_dev(self, net):
        self.reset_state()

        nettype = net.type
        source = net.source
        if net.network:
            # If using type=network with a forward mode=bridge network,
            # on domain startup the runtime XML will be changed to
            # type=bridge and both source/@bridge and source/@network will
            # be filled in. For our purposes, treat this as a type=network
            source = net.network
            nettype = "network"

        source_mode = net.source_mode
        uiutil.set_list_selection(self.widget("net-source-mode"), source_mode)

        # Find the matching row in the net list
        combo = self.widget("net-source")
        rowiter = None
        for row in combo.get_model():
            if row[0] == nettype and row[1] == source:
                rowiter = row.iter
                break
        if not rowiter:
            if nettype == "bridge":
                rowiter = combo.get_model()[-1].iter
                self.widget("net-bridge-name").set_text(source)
        if not rowiter:
            desc = self._pretty_network_desc(nettype, source)
            combo.get_model().insert(0,
                self._build_source_row(nettype, source, desc, True, True))
            rowiter = combo.get_model()[0].iter

        combo.set_active_iter(rowiter)
        combo.emit("changed")

        if net.portgroup:
            uiutil.set_list_selection(self.widget("net-portgroup"), net.portgroup)


    #############
    # Listeners #
    #############

    def _emit_changed(self, *args, **kwargs):
        ignore1 = args
        ignore2 = kwargs
        self.emit("changed")

    def _repopulate_network_list(self, *args, **kwargs):
        ignore1 = args
        ignore2 = kwargs

        netlist = self.widget("net-source")
        current_label = uiutil.get_list_selection(netlist, column=2)

        model = netlist.get_model()
        if not model:
            return

        try:
            if model:
                netlist.set_model(None)
                default_idx = self._populate_network_model(model)
        finally:
            netlist.set_model(model)

        for row in netlist.get_model():
            if current_label and row[2] == current_label:
                netlist.set_active_iter(row.iter)
                return

        if default_idx is None:
            default_idx = 0
        netlist.set_active(default_idx)


    def _populate_portgroups(self, portgroups):
        combo = self.widget("net-portgroup")
        model = combo.get_model()
        model.clear()

        default = None
        for p in portgroups:
            model.append([p.name, p.name])
            if p.default:
                default = p.name

        uiutil.set_list_selection(combo, default)

    def _on_net_source_changed(self, src):
        ignore = src
        self._emit_changed()
        row = self.get_network_row()
        if not row:
            return

        is_direct = (row[0] == virtinst.DeviceInterface.TYPE_DIRECT)
        uiutil.set_grid_row_visible(self.widget("net-source-mode"), is_direct)
        uiutil.set_grid_row_visible(
            self.widget("net-macvtap-warn-box"), is_direct)
        if is_direct and self.widget("net-source-mode").get_active() == -1:
            self.widget("net-source-mode").set_active(0)

        show_bridge = row[5]
        uiutil.set_grid_row_visible(
            self.widget("net-bridge-name"), show_bridge)

        portgroups = []
        connkey = row[6]
        if connkey and row[0] == virtinst.DeviceInterface.TYPE_VIRTUAL:
            portgroups = self.conn.get_net(connkey).get_xmlobj().portgroups

        uiutil.set_grid_row_visible(
            self.widget("net-portgroup"), bool(portgroups))
        self._populate_portgroups(portgroups)
