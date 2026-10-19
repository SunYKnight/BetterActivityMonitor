"""Network page: combined download/upload stacked per interface, plus one card per physical interface."""

from gi.repository import Gtk, Pango

from better_activity_monitor.hwinfo import NetInfo
from better_activity_monitor.pages.base import Page
from better_activity_monitor.pages.disks import mini_stat
from better_activity_monitor.util import DASH, fmt_bytes, fmt_rate, is_num
from better_activity_monitor.widgets import EvenGrid, Series, StatTile, Value, card, color

MIN_SPAN = 1e5
RX_COLOR = color(0)
TX_COLOR = color(7)


def link_speed(mbits: int | None) -> str:
    text = DASH
    if mbits:
        text = f"{mbits / 1000:g} Gbit/s" if mbits >= 1000 else f"{mbits} Mbit/s"
    return text


class NetCard(Gtk.Box):
    def __init__(self, page: Page, net: NetInfo) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["card", "chart-card"])
        self.net = net
        header = Gtk.Box(spacing=12)
        icon = "network-wireless-symbolic" if net.wireless else "network-wired-symbolic"
        header.append(Gtk.Image(icon_name=icon, pixel_size=32, css_classes=["dim-label"]))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        titles.append(Gtk.Label(label=f"{net.name} · {net.kind}", xalign=0, css_classes=["heading"]))
        titles.append(Gtk.Label(label=net.controller, xalign=0, css_classes=["caption", "dim-label"],
                                ellipsize=Pango.EllipsizeMode.END))
        header.append(titles)
        self.state = Value("", css_classes=["state-pill"])
        self.state.set_valign(Gtk.Align.CENTER)
        header.append(self.state)
        self.append(header)

        stats = EvenGrid(100)
        self.values = {}
        fields = [("rx", "Download"), ("tx", "Upload"), ("speed", "Link"), ("rxt", "Received since boot"),
                  ("txt", "Sent since boot")]
        if net.wireless:
            fields.append(("signal", "Signal"))
        for key, caption in fields:
            box, value = mini_stat(caption)
            stats.append(box)
            self.values[key] = value
        self.append(stats)
        self.chart = page.chart([
            Series(f"net.{net.name}.rx", "Download", RX_COLOR), Series(f"net.{net.name}.tx", "Upload", TX_COLOR),
        ], fmt_rate, stackable=False, min_span=MIN_SPAN, height=110)
        self.append(self.chart)
        self.addresses = Value("", xalign=0, wrap=True, selectable=True, css_classes=["caption", "dim-label"])
        self.append(self.addresses)

    def update(self, ctx) -> None:
        name = self.net.name
        details = ctx.sample.info["nets"].get(name, {})
        state = details.get("state", "unknown")
        self.state.update("Connected" if state == "up" else state.capitalize())
        if state == "up":
            self.state.add_css_class("up")
        else:
            self.state.remove_css_class("up")
        self.values["rx"].update(fmt_rate(ctx.value(f"net.{name}.rx")))
        self.values["tx"].update(fmt_rate(ctx.value(f"net.{name}.tx")))
        self.values["speed"].update(link_speed(details.get("speed")) if state == "up" else DASH)
        self.values["rxt"].update(fmt_bytes(details.get("rx_total")))
        self.values["txt"].update(fmt_bytes(details.get("tx_total")))
        if "signal" in self.values:
            signal = details.get("signal")
            self.values["signal"].update(f"{signal:.0f} dBm" if is_num(signal) else DASH)
        lines = [f"MAC {self.net.mac}" if self.net.mac else "", *details.get("addresses", [])]
        if details.get("gateway"):
            lines.append(f"Default gateway {details['gateway']}")
        self.addresses.update("\n".join(line for line in lines if line))


class NetworkPage(Page):
    page_id = "network"
    title = "Network"
    icon = "network-wired-symbolic"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        nets = ctx.inventory.nets

        tiles = EvenGrid()
        self.t_rx = StatTile("Download")
        self.t_tx = StatTile("Upload")
        self.t_rxt = StatTile("Received since boot")
        self.t_txt = StatTile("Sent since boot")
        for tile in (self.t_rx, self.t_tx, self.t_rxt, self.t_txt):
            tiles.append(tile)
        self.content.append(tiles)

        grid = EvenGrid(340)
        for kind, title in (("rx", "Download — all interfaces"), ("tx", "Upload — all interfaces")):
            chart = self.chart([
                Series(f"net.{net.name}.{kind}", net.name, color(index)) for index, net in enumerate(nets)
            ], fmt_rate, min_span=MIN_SPAN, height=180)
            grid.append(card(title, chart, self.legend(chart)))
        self.content.append(grid)

        cards = EvenGrid(340)
        self.cards = [NetCard(self, net) for net in nets]
        for net_card in self.cards:
            cards.append(net_card)
        self.content.append(cards)

    def update(self) -> None:
        details = self.ctx.sample.info["nets"].values()
        up = [entry for entry in details if entry.get("state") == "up"]
        self.t_rx.set(fmt_rate(self.ctx.value("net.rx")), f"{len(up)} of {len(details)} interfaces up")
        self.t_tx.set(fmt_rate(self.ctx.value("net.tx")))
        self.t_rxt.set(fmt_bytes(sum(entry.get("rx_total", 0) for entry in details)))
        self.t_txt.set(fmt_bytes(sum(entry.get("tx_total", 0) for entry in details)))
        for net_card in self.cards:
            net_card.update(self.ctx)

    def summary(self) -> str:
        return f"↓ {fmt_rate(self.ctx.value('net.rx'))} · ↑ {fmt_rate(self.ctx.value('net.tx'))}"

    def spark(self) -> tuple:
        return ("net.total", RX_COLOR, None, MIN_SPAN)
