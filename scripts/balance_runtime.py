"""Simulator-neutral, explicit weighing-state abstraction, SI inputs/outputs.

No contact solver is implemented here. Feed only the pan's supported load force,
excluding pan self-weight. The host simulator must own collisions, gravity,
force aggregation through containers, joint constraints, and object attachment.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math

@dataclass
class Balance:
    capacity_kg: float
    readability_kg: float
    settling_s: float
    analytical: bool=False
    powered: bool=False
    pan_installed: bool=True
    pan_latched: bool=True
    transport_unlocked: bool=True
    security_locked: bool=False
    calibrated: bool=False
    g_cal: float=9.80665
    tare_kg: float=0.0
    zero_kg: float=0.0
    gross_kg: float=0.0
    stable: bool=False
    elapsed_s: float=0.0
    unit: str='g'
    mode: str='weighing'
    ports_connected: bool=False
    door_fraction: float=0.0
    tilt_rad: float=0.0
    last_environment_valid: bool=False

    @classmethod
    def for_model(cls,model):
        if model=='PX224E': return cls(.220,1e-7,3,True)
        if model=='SPX2201': return cls(2.2,.0001,1,False)
        raise ValueError('Unknown model')

    def update(self,pan_load_force_N,dt_s,*,door_fraction=0.0,tilt_rad=0.0,vibration=False):
        """Force is net supported load along pan local +Z; no pan self-force.

        Door and tilt stability gating is a conservative task rule, not a measured
        transfer function. No invented airflow/noise accuracy is asserted.
        """
        if not math.isfinite(pan_load_force_N) or not math.isfinite(dt_s) or dt_s<0:
            raise ValueError('Finite force and nonnegative finite dt required')
        m=pan_load_force_N/self.g_cal
        changed=abs(m-self.gross_kg)>self.readability_kg/2
        self.gross_kg=m; self.door_fraction=door_fraction; self.tilt_rad=tilt_rad
        environmental_gate=(not self.analytical or door_fraction<=.01) and abs(tilt_rad)<=math.radians(.2) and not vibration
        hardware_gate=self.powered and self.pan_installed and self.pan_latched and self.transport_unlocked
        valid=environmental_gate and hardware_gate
        if changed or not valid or not self.last_environment_valid: self.elapsed_s=0
        else: self.elapsed_s+=dt_s
        self.last_environment_valid=valid
        self.stable=bool(hardware_gate and environmental_gate and self.elapsed_s>=self.settling_s and 0<=m<=self.capacity_kg)
        return self.read()

    def read(self):
        if not self.powered: status='OFF'
        elif not self.pan_installed: status='PAN_REMOVED'
        elif not self.pan_latched: status='PAN_UNLATCHED'
        elif not self.transport_unlocked: status='TRANSPORT_LOCKED'
        elif self.gross_kg>self.capacity_kg: status='OVERLOAD'
        elif self.gross_kg<0: status='UNDERLOAD'
        elif not self.stable: status='UNSTABLE'
        else: status='STABLE'
        # Quantize to readability; do not interpret readability as accuracy.
        x=Decimal(str(self.gross_kg))-Decimal(str(self.zero_kg))-Decimal(str(self.tare_kg))
        step=Decimal(str(self.readability_kg))
        value=float((x/step).quantize(Decimal('1'),rounding=ROUND_HALF_UP)*step)
        observable=status in ['STABLE','UNSTABLE']
        scale=1000 if self.unit=='g' else 1
        return {'status':status,'stable':self.stable,'value':value*scale if observable else None,
                'unit':self.unit,'net_kg':value if observable else None,'gross_kg':self.gross_kg,
                'tare_kg':self.tare_kg,'calibrated':self.calibrated,
                'task_ready':status=='STABLE' and self.calibrated}

    def press(self,key,hold_s=.1):
        if hold_s<0: raise ValueError('negative hold time')
        long=hold_s>=2
        if key=='zero':
            if long: self.powered=False; self.stable=False; self.elapsed_s=0
            elif not self.powered: self.powered=True; self.elapsed_s=0; self.stable=False
            elif self.stable:
                # 2% zero capture limit is an explicit task assumption, not a vendor value.
                if abs(self.gross_kg)>.02*self.capacity_kg: raise ValueError('Outside assumed zero capture range')
                self.zero_kg=self.gross_kg; self.tare_kg=0
            else: raise ValueError('Wait for stable load')
        elif not self.powered: raise ValueError('Power is off')
        elif key=='tare':
            if long: return {'event':'open_menu','security_locked':self.security_locked}
            if not self.stable: raise ValueError('Wait for stable load')
            self.tare_kg=self.gross_kg-self.zero_kg
        elif key=='print':
            if long: self.unit='kg' if self.unit=='g' else 'g'
            else: return {'event':'print','transmitted':self.ports_connected,'reading':self.read()}
        elif key=='function':
            return {'event':'mode_menu' if long else 'application_function','mode':self.mode,'implemented':self.mode=='weighing'}
        else: raise ValueError('Unknown key')
        return self.read()

    def calibrate_span(self,known_mass_kg,measured_load_force_N):
        """Simplified one-point force gain calibration, not full vendor procedure."""
        if not self.powered or not self.stable or self.security_locked:
            raise ValueError('Calibration requires stable powered balance with security unlocked')
        if not 0<known_mass_kg<=self.capacity_kg or measured_load_force_N<=0:
            raise ValueError('Invalid calibration load')
        self.g_cal=measured_load_force_N/known_mass_kg
        self.tare_kg=0; self.zero_kg=0; self.calibrated=True; self.elapsed_s=0; self.stable=False

    def remove_pan(self,unlock_angle_rad=0.0):
        if self.powered or abs(self.gross_kg)>self.readability_kg:
            raise ValueError('Power off and unload before pan removal')
        if not self.analytical and unlock_angle_rad>-math.radians(20)+1e-9:
            raise ValueError('Rotate clockwise to assumed -20 degree bayonet release first')
        self.pan_installed=False; self.pan_latched=False; self.stable=False
        return {'event':'release_attachment','free_dof':6}

    def install_pan(self,latched=True):
        self.pan_installed=True; self.pan_latched=latched; self.stable=False; self.elapsed_s=0

    def set_transport_lock(self,angle_rad):
        if self.analytical: raise ValueError('PX224E has no modeled user transport lock')
        if self.pan_installed: raise ValueError('Transport lock is beneath pan; remove pan first')
        if not 0<=angle_rad<=math.pi/2: raise ValueError('Manufacturer range is 0 to 90 degrees')
        self.transport_unlocked=abs(angle_rad-math.pi/2)<1e-6
        self.stable=False; self.elapsed_s=0

def foot_extension_m(theta_rad,pitch_m_per_turn=.001):
    if abs(theta_rad)>4*math.pi: raise ValueError('Outside assumed leveling range')
    return pitch_m_per_turn*theta_rad/(2*math.pi)
