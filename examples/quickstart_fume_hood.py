"""Drive the Esco fume hood (CHEM-199) through its contract API.

The agent raises the sash, notices that the uncalibrated airflow monitor reads low,
measures the face velocity with a handheld anemometer and calibrates the monitor.

Run from the repository root:  python examples/quickstart_fume_hood.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asset_runtime import esco_efa5uduvw as rt  # noqa: E402

hood = rt.EscoEfa5uduvwRuntime("hood_01")
hood.register_principal("lab", "evaluator")  # sets up the world and can see hidden truth
hood.register_principal("robot", "agent")    # sees only what a policy may see
lab, robot = hood.session("lab"), hood.session("robot")
_n = 0


def call(session, capability, **arguments):
    global _n
    _n += 1
    r = session.invoke(instance_id="hood_01", capability_id=capability, arguments=arguments, request_id=f"r{_n}")
    if r["status"] != "succeeded":
        raise RuntimeError(f"{capability}: {r['error']}")
    return r["result"]


def show(label):
    obs = robot.read(instance_id="hood_01", state_ids=["monitor.reading_m_s", "monitor.alarm"])["observations"]
    print(f"{label:20s} reading {obs['monitor.reading_m_s']['value']:.2f} m/s   alarm {obs['monitor.alarm']['value'] or 'none'}")


call(lab, "plant.configure", **rt.PLANT_EXAMPLE)          # ambient 295.15 K, 101.325 kPa
call(lab, "exhaust.configure", **rt.EXHAUST_EXAMPLE)      # building exhaust, damper half open
call(robot, "light.set", on=True)
call(robot, "sash.set", height_m=0.457)                    # raise the sash to the 18-inch stop
call(lab, "sim.step", dt_s=30.0)                           # let the airflow settle
show("before calibration")

v = call(robot, "anemometer.measure")["face_velocity_m_s"]  # 18-point traverse of the sash opening
print(f"{'anemometer':20s} {v:.3f} m/s")
call(robot, "monitor.calibrate", reference_m_s=round(v, 3))
call(lab, "sim.step", dt_s=10.0)
show("after calibration")

print("agent-visible capabilities:", len(robot.describe(instance_id="hood_01")["capabilities"]))
