"""Chart helpers for the analytics page (Altair, bundled with Streamlit). Colours come from a validated palette:
categorical slots in a fixed order (never cycled), one blue ramp for magnitudes, and its own steps in dark mode.
Every chart has a tooltip, and the page puts a data table next to it, so no value is told by colour alone."""
import altair as alt
import pandas as pd
import streamlit as st

# Fixed order: passes the colour-blindness checks for adjacent series in light and dark (validated against
# Streamlit's white and #0e1117 backgrounds). Two light slots are under 3:1, hence the data tables.
CATEGORICAL = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
               "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"]}
SEQUENTIAL = {"light": ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#104281"],  # low -> high
              "dark": ["#184f95", "#256abf", "#3987e5", "#86b6ef", "#cde2fb"]}
SURFACE = {"light": "#ffffff", "dark": "#0e1117"}
ALERT = "#d03b3b"  # status "critical": only for the alert threshold line, always with a label


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except AttributeError:
        return "light"


def _show(chart: alt.Chart) -> None:
    st.altair_chart(chart.configure_view(strokeWidth=0).configure_axis(grid=True, gridOpacity=0.25),
                    width="stretch", theme="streamlit")


def bars(df: pd.DataFrame, x: str, y: str, x_title: str, *, tooltip: list, fmt: str = "", rule: float | None = None,
         rule_label: str = "", x_max: float | None = None, height: int = 220) -> None:
    """One series, horizontal bars (no legend: the title names it), with an optional alert threshold."""
    m = mode()
    base = alt.Chart(df).encode(y=alt.Y(f"{y}:N", title=None, sort="-x", axis=alt.Axis(labelOverlap=False)))
    chart = base.mark_bar(cornerRadiusEnd=4, size=14, color=CATEGORICAL[m][0]).encode(
        x=alt.X(f"{x}:Q", title=x_title, axis=alt.Axis(format=fmt, tickCount=5) if fmt else alt.Axis(tickCount=5),
                scale=alt.Scale(domain=[0, x_max]) if x_max else alt.Scale()), tooltip=tooltip)
    if rule is not None:
        line = alt.Chart(pd.DataFrame({"v": [rule], "label": [rule_label]}))
        chart = chart + line.mark_rule(color=ALERT, strokeDash=[4, 3], strokeWidth=2).encode(x="v:Q") + \
            line.mark_text(color=ALERT, align="left", dx=4, dy=-6, baseline="bottom").encode(x="v:Q", text="label:N", y=alt.value(0))
    _show(chart.properties(height=height))


def stacked(df: pd.DataFrame, x: str, y: str, color: str, order: list[str], x_title: str, color_title: str, *,
            tooltip: list, height: int = 220) -> None:
    """Up to 4 series stacked; fixed colour per category (it follows the category, not its rank); 2px gaps."""
    m = mode()
    chart = alt.Chart(df).mark_bar(size=14, stroke=SURFACE[m], strokeWidth=2).encode(
        x=alt.X(f"{x}:Q", title=x_title, stack="zero", axis=alt.Axis(tickCount=5)),
        y=alt.Y(f"{y}:N", title=None, axis=alt.Axis(labelOverlap=False)),
        color=alt.Color(f"{color}:N", title=color_title,
                        scale=alt.Scale(domain=order, range=CATEGORICAL[m][:len(order)]),
                        legend=alt.Legend(orient="top")),
        order=alt.Order(f"{color}_order:Q"), tooltip=tooltip)
    _show(chart.properties(height=height))


def lines(df: pd.DataFrame, x: str, y: str, color: str, order: list[str], slots: list[int], y_title: str, *,
          fmt: str = "", height: int = 240) -> None:
    """Change over time; up to 4 series with a legend; hover shows every series on that day. `slots` gives each
    series its fixed palette slot, so a team keeps its colour when other teams are hidden or filtered out."""
    m = mode()
    enc = dict(x=alt.X(f"{x}:T", title=None), y=alt.Y(f"{y}:Q", title=y_title, axis=alt.Axis(format=fmt) if fmt else alt.Axis()),
               color=alt.Color(f"{color}:N", title=None,
                               scale=alt.Scale(domain=order, range=[CATEGORICAL[m][i] for i in slots]),
                               legend=alt.Legend(orient="top")))
    hover = alt.selection_point(fields=[x], nearest=True, on="pointerover", empty=False)
    base = alt.Chart(df).encode(**enc)
    chart = base.mark_line(strokeWidth=2) + \
        base.mark_point(size=64, filled=True).encode(opacity=alt.condition(hover, alt.value(1), alt.value(0)),
                                                      tooltip=[f"{x}:T", f"{color}:N", alt.Tooltip(f"{y}:Q", format=fmt or "")]
                                                      ).add_params(hover) + \
        alt.Chart(df).mark_rule(opacity=0.3).encode(x=f"{x}:T").transform_filter(hover)
    _show(chart.properties(height=height))


def heatmap(df: pd.DataFrame, x: str, y: str, value: str, value_title: str, *, tooltip: list, domain_max: float | None = None,
            height: int | None = None) -> None:
    """Magnitude per cell on one blue ramp (light = little). Cells are separated by a 2px gap."""
    m = mode()
    rows = max(1, df[y].nunique())
    chart = alt.Chart(df).mark_rect(stroke=SURFACE[m], strokeWidth=2, cornerRadius=3).encode(
        x=alt.X(f"{x}:N", title=None, axis=alt.Axis(labelAngle=-30, orient="top", labelLimit=220)),
        y=alt.Y(f"{y}:N", title=None, axis=alt.Axis(labelLimit=260)),
        color=alt.Color(f"{value}:Q", title=value_title,
                        scale=alt.Scale(range=SEQUENTIAL[m], domain=[0, domain_max] if domain_max else alt.Undefined),
                        legend=alt.Legend(orient="right", gradientLength=120)),
        tooltip=tooltip)
    _show(chart.properties(height=height or 34 * rows + 60))
