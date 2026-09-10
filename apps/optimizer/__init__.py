"""
apps/optimizer — Fuel-stop selection solver
===========================================
Pure-Python, framework-agnostic module.  Given a route geometry and a set
of candidate fuel stations (with prices and coordinates), it solves the
cost-optimal refuelling sequence as a shortest-path problem over a DAG of
reachable stations within the vehicle's range.
"""
