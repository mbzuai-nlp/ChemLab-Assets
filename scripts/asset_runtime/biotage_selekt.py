"""Selekt SEL-2SV: sequential channels, finite carriers, conservative conditional chromatograms."""
from __future__ import annotations
import copy,importlib.util,json,math,sys
from pathlib import Path
import numpy as np
class ActionRejected(ValueError):
 """A capability call refused with a declared error code."""
def number(unit,lo=None,hi=None):
 d={'type':'number','x-unit':unit}
 if lo is not None:d['minimum']=lo
 if hi is not None:d['maximum']=hi
 return d
def obj(props,optional=()):return dict(type='object',properties=props,required=[k for k in props if k not in optional],additionalProperties=False)
BOOL={'type':'boolean'};TEXT={'type':'string'}
def _shared_validate(schema,value):
 """Explicit supported JSON subset (the shared CHEM-series validator, kept here): finite numbers, strict bool / array / const handling."""
 if 'anyOf' in schema:
  for sub in schema['anyOf']:
   try:_shared_validate(sub,value);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 typ=schema.get('type')
 if typ=='number':
  if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif typ=='boolean':
  if type(value) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif typ=='null':
  if value is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif typ=='string':
  if not isinstance(value,str) or len(value)>schema.get('maxLength',10**6) or len(value)<schema.get('minLength',0):raise ActionRejected('INVALID_ARGUMENT')
 elif typ=='array':
  if not isinstance(value,list) or not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for v in value:_shared_validate(schema['items'],v)
 elif typ=='object':
  if not isinstance(value,dict) or not set(schema.get('required',[]))<=value.keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,v in value.items():
   if not isinstance(k,str):raise ActionRejected('INVALID_ARGUMENT')
   sub=schema.get('properties',{}).get(k,schema.get('additionalProperties',True))
   if sub is False:raise ActionRejected('INVALID_ARGUMENT')
   if isinstance(sub,dict):_shared_validate(sub,v)
 if 'enum' in schema and value not in schema['enum']:raise ActionRejected('INVALID_ARGUMENT')
 if 'const' in schema and value!=schema['const']:raise ActionRejected('INVALID_ARGUMENT')
def validate(schema,value):
 """Retain shared finite-value validation and enforce nested integer schemas locally."""
 if schema.get('type')=='integer':
  if type(value) is not int or not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 if schema.get('type')=='object' and isinstance(value,dict):
  for key,child in value.items():
   sub=schema.get('properties',{}).get(key,schema.get('additionalProperties',True))
   if isinstance(sub,dict):validate(sub,child)
 if schema.get('type')=='array' and isinstance(value,list):
  for child in value:validate(schema['items'],child)
 _shared_validate(schema,value)
ROOT=Path(__file__).resolve().parents[2];SLUG='biotage_selekt';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-034';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/biotage_selekt_uv_library/templates.json';N=12
def arr(s,n=None):return dict(type='array',items=s,**(dict(minItems=n,maxItems=n) if n else {}))
def integer(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
CHANNEL=integer(0,1);PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'solvent_compatibility_confirmed':BOOL,'fume_hood_confirmed':BOOL,'unused_channel_connected':BOOL,'spectral_transfer_assumption_accepted':BOOL,'solvent_volumes_m3':arr(number('m^3',1e-9,.1),4),'waste_capacity_m3':number('m^3',1e-9,.1),'mixer_volume_m3':number('m^3',1e-7,1e-5),'detector_volume_m3':number('m^3',1e-7,1e-5),'optical_path_m':number('m',1e-5,.01),'uv_response_scale':number('1',.001,1000),'collector_speed_m_s':number('m/s',.01,1),'release_tau_s':number('s',.1,100),'ambient_pa':number('Pa',8e4,1.2e5),'coefficient_evidence':TEXT})
EXAMPLE=dict(solvent_compatibility_confirmed=True,fume_hood_confirmed=True,unused_channel_connected=True,spectral_transfer_assumption_accepted=True,solvent_volumes_m3=[.002]*4,waste_capacity_m3=.002,mixer_volume_m3=1e-6,detector_volume_m3=5e-7,optical_path_m=.001,uv_response_scale=1.,collector_speed_m_s=.2,release_tau_s=2.,ambient_pa=101325.,coefficient_evidence='uncalibrated_host_geometry_response_transport')
COLUMN=obj({'channel':CHANNEL,'void_volume_m3':number('m^3',1e-6,100e-6),'resistance_pa_s_m3':number('Pa*s/m^3',1e8,1e14),'strong_solvent_resistance_factor':number('1',.1,10),'pressure_limit_pa':number('Pa',1e4,3e6),'max_sample_amount_mol':number('mol',1e-9,.01),'conditioned_strong_fraction':number('1',0,1),'solvent_pair':arr(integer(0,3),2),'column_compatibility_confirmed':BOOL,'coefficient_evidence':TEXT})
COLUMN_EXAMPLE=dict(channel=0,void_volume_m3=12e-6,resistance_pa_s_m3=2e12,strong_solvent_resistance_factor=1.,pressure_limit_pa=3e6,max_sample_amount_mol=1e-4,conditioned_strong_fraction=0.,solvent_pair=[0,1],column_compatibility_confirmed=True,coefficient_evidence='synthetic12cell_linear_equilibrium_column_not_fitted')
SPECIES=obj({'substance_id':TEXT,'amount_mol':number('mol',1e-12,.001),'k0':number('1',0,10000),'solvent_strength_slope':number('1',0,30)})
SAMPLE=obj({'channel':CHANNEL,'sample_id':TEXT,'species':dict(type='array',items=SPECIES,minItems=1,maxItems=8),'dilute_linear_load_confirmed':BOOL})
SAMPLE_EXAMPLE=dict(channel=0,sample_id='two_aromatics',species=[dict(substance_id='pcad_022',amount_mol=2e-6,k0=2.,solvent_strength_slope=3.),dict(substance_id='pcad_034',amount_mol=1e-6,k0=6.,solvent_strength_slope=4.)],dilute_linear_load_confirmed=True)
NODE=obj({'time_s':number('s',0,7200),'strong_fraction':number('1',0,1)})
METHOD=obj({'channel':CHANNEL,'flow_m3_s':number('m^3/s',1e-6/60,300e-6/60),'gradient':dict(type='array',items=NODE,minItems=2,maxItems=20),'wavelengths_m':arr(number('m',200e-9,400e-9),2),'collection_mode':dict(type='string',enum=['all','threshold']),'threshold_absorbance':number('1',0,6.4),'fraction_volume_m3':number('m^3',1e-8,9e-6),'timeout_s':number('s',1,21600)})
METHOD_EXAMPLE=dict(channel=0,flow_m3_s=30e-6/60,gradient=[dict(time_s=0.,strong_fraction=0.),dict(time_s=240.,strong_fraction=.8)],wavelengths_m=[300e-9,350e-9],collection_mode='threshold',threshold_absorbance=.02,fraction_volume_m3=5e-6,timeout_s=1200.)
RACK=obj({'positions':integer(1,144),'vessel_volume_m3':number('m^3',1e-8,9e-6)})
OBS=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'channel':CHANNEL,'pressure_gauge_pa':number('Pa',0),'flow_m3_s':number('m^3/s',0),'strong_fraction':number('1',0,1),'uv_absorbance':arr(number('1',0,6.4),2),'fraction_number':integer(0,144),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'substance_ids':arr(TEXT),'initial_amount_mol':arr(number('mol',0)),'column_amount_mol':arr(number('mol',0)),'detector_amount_mol':arr(number('mol',0)),'waste_amount_mol':arr(number('mol',0)),'fraction_amount_mol':arr(arr(number('mol',0))),'fraction_volume_m3':arr(number('m^3',0)),'waste_volume_m3':number('m^3',0),'measured_Selekt_recovery':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
HREC=obj({'time_s':number('s',0),'channel':CHANNEL,'elapsed_s':number('s',0),'uv1':number('1',0,6.4),'uv2':number('1',0,6.4),'pressure_pa':number('Pa',0),'strong_fraction':number('1',0,1),'fraction_number':integer(0,144),'stage':TEXT})
REF=obj({'substance_id':TEXT,'wavelength_m':number('m',200e-9,400e-9),'epsilon_m2_mol':number('m^2/mol'),'solvent_description':TEXT,'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),EXAMPLE,['pump','uv_cell'],'configuration','evaluator','配置外部有限溶剂与假设机理'),
 'column.configure':(COLUMN,obj({'configured':BOOL}),COLUMN_EXAMPLE,['column_0','column_1'],'configuration','evaluator','登记已平衡柱与流阻'),
 'sample.configure':(SAMPLE,obj({'configured':BOOL}),SAMPLE_EXAMPLE,['column_0','column_1'],'configuration','evaluator','宿主将有限稀负载装入柱首单元'),
 'method.configure':(METHOD,obj({'configured':BOOL}),METHOD_EXAMPLE,['pump','uv_cell','selector_valve'],'device_command','agent','设置流速、梯度与双UV收集阈值'),
 'rack.configure':(RACK,obj({'configured':BOOL}),dict(positions=144,vessel_volume_m3=9e-6),['tray'],'configuration','evaluator','登记管架位置数及有限每管容量'),
 'tray.set_extension':(obj({'extension_m':number('m',0,.12)}),obj({'extension_m':number('m',0,.12)}),dict(extension_m=.12),['tray'],'configuration','evaluator','宿主手动托盘位姿'),
 'channel.select':(obj({'channel':CHANNEL}),obj({'channel':CHANNEL}),dict(channel=0),['selector_valve'],'device_command','agent','选择下一个顺序柱通道'),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),dict(enabled=True),['pump','touchscreen'],'device_command','agent','控制电源，断电停止但保留库存和残压'),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),{},['pump','uv_cell','collector_x','collector_y'],'device_command','agent','启动选中柱有限时长梯度洗脱与自动卸压'),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),{},['pump'],'device_command','agent','取消当前作业并保留部分效果'),
 'pressure.release':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),{},['selector_valve'],'device_command','agent','显式卸压，不删除柱残留量'),
 'chromatography.measure':(obj({}),OBS,{},['uv_cell','touchscreen'],'query','agent','读取条件模型色谱观测'),
 'product.read':(obj({}),PRODUCT,{},['tray','column_0','column_1'],'query','evaluator','读取各物种与144管产物账本'),
 'history.read':(obj({}),obj({'records':arr(HREC),'evidence':TEXT}),{},['uv_cell'],'query','agent','读取观测历史，无组成真值'),
 'reference.lookup':(obj({'substance_id':TEXT,'wavelength_m':number('m',200e-9,400e-9)}),REF,dict(substance_id='pcad_022',wavelength_m=350e-9),['uv_cell'],'query','agent','按实测光谱支持范围查参考消光系数')}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',vis='agent',component='pump',write=None):SLOTS[k]=(s,u,o,vis,[component],write);SLOT_ZH[k]=zh
for k,s,u,zh in [('power.enabled',BOOL,'1','控制电源'),('plant.configured',BOOL,'1','宿主边界配置完成'),('rack.configured',BOOL,'1','有限管架配置完成'),('channel.selected',CHANNEL,'1','当前顺序柱通道'),('cycle.phase',PHASE,'1','作业状态'),('cycle.stage',TEXT,'1','过程阶段'),('cycle.fault',TEXT,'1','最近过程故障'),('cycle.elapsed_s',number('s',0),'s','当前作业时长，含卸压'),('cycle.gradient_s',number('s',0),'s','当前柱实际梯度流动时间'),('flow.target_m3_s',number('m^3/s',0),'m^3/s','液体流率目标'),('flow.actual_m3_s',number('m^3/s',0),'m^3/s','当前模型液体流率'),('gradient.target_fraction',number('1',0,1),'1','当前方法梯度目标强溶剂比例'),('gradient.mixer_fraction',number('1',0,1),'1','混合器实际强溶剂体积分数'),('pressure.gauge_pa',number('Pa',0),'Pa','所选柱模型表压'),('uv.absorbance_1',number('1',0,6.4),'1','UV1预测十进吸光度，非原机校准测量'),('uv.absorbance_2',number('1',0,6.4),'1','UV2预测十进吸光度'),('collection.fraction_number',integer(0,144),'1','当前收集管编号；零为废液/移动'),('collection.positions_used',integer(0,144),'1','已接收液体的管数'),('collector.moving',BOOL,'1','收集针尚在移动')]:slot(k,s,u,zh,o='setpoint' if 'target' in k else 'derived')
for i in range(2):
 for name,s,u,zh in [('column_ready',BOOL,'1','柱已配置'),('sample_loaded',BOOL,'1','柱已装样'),('method_ready',BOOL,'1','柱方法已配置'),('pressure_pa',number('Pa',0),'Pa','该柱保留的表压')]:slot(f'channel.c{i}.{name}',s,u,str(i+1)+'通道'+zh,component='column_'+str(i))
for k,hi,zh,c in [('tray.extension_m',.12,'手动托盘前伸位移','tray'),('collector.x_m',.126,'收集针横向位移','collector_x'),('collector.y_m',.33,'收集针前后位移','collector_y')]:slot(k,number('m',0,hi),'m',zh,o='scene',component=c)
for k,zh in [('initial','初始输入'),('column','两柱总残留'),('detector','检测池残留'),('fractions','全部收集管中'),('waste','废液中')]:slot('truth.'+k+'_amount_mol',number('mol',0),'mol',zh+'溶质物质的量',o='process_truth',vis='evaluator')
for i in range(4):
 for k,zh in [('initial','初始总计'),('reservoir','进料瓶'),('holdup','管路及两柱持液'),('fractions','收集管'),('waste','废液')]:slot(f'truth.solvent_{i}_{k}_m3',number('m^3',0),'m^3',str(i+1)+'号液体溶剂'+zh+'体积',o='process_truth',vis='evaluator')


# ---------------------------------------------------------------- touch-screen software (terminal layer; Biotage Selekt User Manual 2020, 'manual p.N')
# Run setup per channel (Information, Column, Gradient, Collection, Rack panels), Start with the Sample Load dialog, pause / end / hold / manual
# fractionation / manual UV zero / extend, Solvent Setup with monitoring and prime, Flushes and Purge, Results, Data Administration, System
# Settings, Maintenance, Shut Down.  The panel spec (panel_ui.json) drives these commands and the process commands above.
ASSET_VERSION='1.2.1'
ASSET_DIR=ROOT/'assets/expansion_batch03/t2_vacuum/biotage_selekt'
ML=1e-6                                                                                   # m3 per mL
PRIME_ML,PRIME_ML_MIN=27.,100.                                                            # 27 mL per prime (manual p.7); prime flow 100 mL/min (assumption)
LINE_FLUSH_ML,DETECTOR_FLUSH_ML,CLEANUP_ML_MIN=10.,5.,50.                                 # end-of-run flush volumes and flow (assumption: not stated)
CONVERSION_ML=PRIME_ML                                                                   # each solvent of an automatic phase conversion: one prime volume (assumption)
EXTEND_FRACTION=.25                                                                       # Auto Extend / Extend Run: +25 % of the gradient length (manual p.16-17)
OWNER,OWNER_PASSWORD='System Owner','1234'                                                # first log-in to the administration views (manual p.26)
USES=['Collect','Show','Hide']
RACK_TYPES={'48x13x100 mm (9 mL)':(48,9.),'35x16x100 mm (14 mL)':(35,14.),'35x16x150 mm (22 mL)':(35,22.),'28x18x150 mm (27 mL)':(28,27.),
            '15x25x150 mm (45 mL)':(15,45.),'6x120 mL':(6,120.),'10x240 mL':(10,240.),'8x480 mL':(8,480.)}            # manual p.1: positions, vessel volume
# preconfigured Biotage column types (Column Administration, manual p.25): names, CV, default flow, max pressure, equilibration and air flush lengths,
# load capacity at dCV=2 - values are assumptions for Sfar Silica D columns (the real list ships with the software)
COLUMN_TYPES={n:dict(name=n,visible=True,builtin=True,cv_ml=cv,default_flow_ml_min=f,max_pressure_bar=20.,equil_pressure_bar=5.,equil_length_cv=3.,
                     equil_flow_ml_min=f,air_flush_length_cv=3.,load_capacity_g=cap,chemistry='normal',part_number=pn)
              for n,cv,f,cap,pn in [('Sfar Silica D 5g',7.,12.,.25,'FSRD-0445-0005'),('Sfar Silica D 10g',15.,12.,.5,'FSRD-0445-0010'),
                                    ('Sfar Silica D 25g',33.,25.,1.25,'FSRD-0445-0025'),('Sfar Silica D 50g',66.,50.,2.5,'FSRD-0445-0050'),
                                    ('Sfar Silica D 100g',132.,100.,5.,'FSRD-0445-0100'),('Sfar C18 D 12g',17.,12.,.12,'FSUD-0409-0012')]}
COLUMN_TYPES['Sfar C18 D 12g']['chemistry']='reversed'
# preconfigured solvents (Solvent Administration, manual p.25-26): strength 0-1.1 (eps0 on silica, Lange's Handbook as the manual cites), max
# aspiration 420 mL/min for the full 300 mL/min (manual p.26); the list itself is an assumption
SOLVENTS={n:dict(name=n,visible=True,builtin=True,strength=s,max_aspiration_ml_min=420.) for n,s in
          [('Heptane',.01),('Hexane',.01),('Cyclohexane',.04),('Toluene',.29),('Dichloromethane',.42),('Ethyl acetate',.58),('Acetone',.56),
           ('Acetonitrile',.65),('Isopropanol',.82),('Ethanol',.88),('Methanol',.95),('Water',1.1)]}
SETTINGS_DEFAULT=dict(date=None,time=None,language='English',pressure_unit='bar',audible_alarm=True,led_light=True,mouse_pointer=False,rfid=True,
                      solvent_monitoring=False,waste_monitoring=False,valley_fractionation=False,manual_uv_zero=True,auto_extend=False,confirm_cleanup=False,
                      start_collection='First Empty Rack',line_flush='Off',detector_flush='Off')
# factory settings are not stated: flushes, extension and monitoring start off so a run behaves as the process model without them (assumption);
# UV Baseline Correction needs a Spektra licence and the Optional Detectors need absent hardware, so they are not offered (manual p.27-29)
FLUSH_OPTIONS=['Off','Collect','To Waste']
SETTING_CHOICES=dict(language=['English'],pressure_unit=['bar','psi'],start_collection=['First Empty Vessel','First Empty Row','First Empty Rack'],
                     line_flush=FLUSH_OPTIONS,detector_flush=FLUSH_OPTIONS)
REPORT_KEYS=['uv1','uv2','gradient','analysis','gradient_table','tlc','racks','channel','comments','report_note','log','unit']   # Report Setup (manual p.21)
RACK_SIZE,ROW_SIZE=48,8                                                                   # host rack = racks A, B, C of 48 vessels (13 x 100 mm), rows of 8
NUL=lambda s:{'anyOf':[s,{'type':'null'}]}                                                   # noqa: E731
STR=lambda n=80:dict(type='string',maxLength=n)                                              # noqa: E731
FREE={'type':'object'}
SEG=obj({'start_pct':number('%',0,100),'end_pct':number('%',0,100),'length_cv':number('CV',.01,1000)})
SETUP=dict(type='object',properties=dict(user=NUL(STR()),run_name=STR(),comments=STR(400),sample_mass_g=NUL(number('g',0,1000)),column_type=NUL(STR()),
                                         flow_ml_min=NUL(number('mL/min',1,300)),unit=dict(type='string',enum=['CV','mL','mm:ss']),segments=dict(type='array',items=SEG,minItems=1,maxItems=10),
                                         solvent_a=NUL(STR()),solvent_b=NUL(STR()),modifier=NUL(STR()),modifier_pct=number('%',0,100),rack_type=NUL(STR()),
                                         equilibration=BOOL,uv1_use=dict(type='string',enum=USES),
                                         uv2_use=dict(type='string',enum=USES),uv1_nm=number('nm',200,400),uv2_nm=number('nm',200,400),threshold_mau=number('mAU',1,6400),
                                         collect_all=BOOL,max_fraction_ml=NUL(number('mL',.1,9)),racks=dict(type='array',items=dict(type='string',enum=['A','B','C']),maxItems=3),tlc=NUL(FREE)),
           additionalProperties=False)
TCAPS={
 'run.setup':(obj({'channel':CHANNEL,'settings':SETUP}),obj({'setup':FREE}),dict(channel=0,settings=dict(flow_ml_min=30,threshold_mau=20)),['touchscreen'],'device_command','agent',
              '设定某通道的运行参数（Information/Column/Gradient/Collection/Rack面板：用户、运行名、注释、样品量、柱类型、流速、梯度单位与段、溶剂A/B、平衡、UV1/UV2用途与波长、阈值、全收集、最大馏分体积、管架）；运行中只能改注释、流速、收集参数与梯度'),
 'column.scan':(obj({'channel':CHANNEL,'column_type':STR(),'column_id':STR(40)}),obj({'setup':FREE}),dict(channel=0,column_type='Sfar Silica D 10g',column_id='SF10-000123'),
                ['qr_reader','touchscreen'],'robot_skill','agent','把Sfar柱的QR码对准触屏下方读码器：设定柱类型并记录柱ID（追溯该柱的运行）'),
 'run.prepare':(obj({'channel':CHANNEL}),obj({'channel':CHANNEL,'issues':dict(type='array',items=TEXT)}),dict(channel=0),['touchscreen'],'device_command','agent',
                '按开始键：检查运行参数（缺失或错误时列出问题，开始键不可用）、溶剂与废液余量（监控开启时），选择通道并打开Sample Load对话框等待上样'),
 'run.unprepare':(obj({}),obj({'channel':NUL(CHANNEL)}),{},['touchscreen'],'device_command','agent','关闭Sample Load对话框，不开始运行'),
 'run.begin':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),{},['touchscreen','pump','uv_cell'],'device_command','agent',
              'Sample Load对话框中开始梯度：按通道设定生成方法（method.configure）并开始（acquisition.start）；样品须已上到柱上'),
 'run.pause':(obj({}),obj({'paused':BOOL}),{},['touchscreen','pump'],'device_command','agent','暂停运行：泵停、收集臂回原位（系统可能仍带压）'),
 'run.resume':(obj({}),obj({'paused':BOOL}),{},['touchscreen','pump'],'device_command','agent','从暂停处继续运行（监控提示的补液/倒废液完成后）'),
 'run.end':(obj({'option':dict(type='string',enum=['skip_equilibration','abort','cleanup'])}),obj({'phase':PHASE,'stage':TEXT}),dict(option='cleanup'),['touchscreen','pump'],'device_command','agent',
            '结束键后System Paused对话框：Abort结束且不冲洗；Cleanup结束并按系统设置做管线冲洗、卸压与检测器冲洗；Skip Equilibration只在平衡阶段（本模型柱已由宿主平衡）'),
 'run.hold':(obj({'on':BOOL}),obj({'hold':BOOL}),dict(on=True),['touchscreen'],'device_command','agent','色谱图中的Hold：保持当前溶剂组成做等度段，关闭后继续梯度（梯度时间顺延）'),
 'run.next_vessel':(obj({}),obj({'fraction_number':integer(0,144)}),{},['touchscreen','collector_x'],'device_command','agent','在色谱图上点按：收集时换到下一个收集管（手动分馏）'),
 'run.uv_zero':(obj({}),obj({'uv_zero_au':dict(type='array',items=number('1'))}),{},['touchscreen','uv_cell'],'device_command','agent','手动UV归零：把当前UV1/UV2吸光度设为0 AU（系统设置开启时）'),
 'run.extend':(obj({}),obj({'gradient_end_s':number('s',0)}),{},['touchscreen'],'device_command','agent','延长运行：以最终条件把梯度延长总梯度长度的25 %（梯度完成对话框中Extend Run或运行中）'),
 'solvents.setup':(obj({'inlet':integer(0,3),'solvent':NUL(STR()),'capacity_ml':NUL(number('mL',1,50000)),'level_ml':NUL(number('mL',0,50000))},optional=('capacity_ml','level_ml')),
                   obj({'inlet':integer(0,3)}),dict(inlet=0,solvent='Heptane',capacity_ml=4000.,level_ml=4000.),['solvent_0','touchscreen'],'device_command','agent',
                   'Menu > Solvent Setup：给进液口S1–S4指定溶剂，监控开启时输入储液瓶容量与当前液量'),
 'waste.setup':(obj({'capacity_ml':NUL(number('mL',1,50000)),'level_ml':NUL(number('mL',0,50000))},optional=('capacity_ml','level_ml')),obj({'capacity_ml':NUL(number('mL'))}),dict(capacity_ml=4000.,level_ml=0.),
                ['waste_port','touchscreen'],'device_command','agent','Menu > Solvent Setup：废液瓶容量与当前液量（废液监控开启时）'),
 'solvents.prime':(obj({'inlet':integer(0,3)}),obj({'phase':PHASE,'operation_id':TEXT}),dict(inlet=0),['pump','solvent_0'],'device_command','agent',
                   'Solvent Setup中Prime：以该进液口的溶剂经旁路冲27 mL到废液（排气泡、换溶剂）'),
 'flush.start':(obj({'path':dict(type='string',enum=['bypass','channel_1','channel_2']),'column_type':NUL(STR()),'volume_ml':number('mL',1,5000),
                     'flow_ml_min':number('mL/min',1,300),'percents':dict(type='array',items=number('%',0,100),minItems=4,maxItems=4)}),obj({'phase':PHASE,'operation_id':TEXT}),
                dict(path='bypass',column_type=None,volume_ml=30.,flow_ml_min=50,percents=[0.,0.,0.,100.]),['pump','selector_valve'],'device_command','agent',
                'Menu > Flushes and Purge > Flush：按路径（旁路或通道）、柱类型、体积、流速与各溶剂百分比冲洗到废液'),
 'flush.air':(obj({'channel':CHANNEL,'column_type':NUL(STR()),'volume_ml':NUL(number('mL',1,10000))},optional=('volume_ml',)),obj({'phase':PHASE,'operation_id':TEXT}),
              dict(channel=0,column_type='Sfar Silica D 10g'),['pump','selector_valve'],'device_command','agent',
              'Flushes and Purge > Air Flush：用空气把柱中剩余溶剂吹到废液（默认体积=柱类型的Air Flush Length×CV）'),
 'results.update':(obj({'run_id':TEXT,'favorite':BOOL,'note':STR(400),'apply_report_setup':BOOL,'report':FREE,'add_picture':FREE,'delete_picture':integer(0,19)},
                       optional=('favorite','note','apply_report_setup','report','add_picture','delete_picture')),
                   obj({'run_id':TEXT}),dict(run_id='run_1',favorite=True),['touchscreen'],'device_command','agent','Results：收藏、报告注释（Report Setup）、报告内容开关与Apply Report Setup'),
 'results.export':(obj({'run_id':TEXT}),obj({'files':dict(type='array',items=TEXT)}),dict(run_id='run_1'),['touchscreen','usb_0'],'device_command','agent',
                   'Results > Export：把报告PDF与XML、SPECTRUM结果文件导出到U盘\\biotage\\selekt'),
 'results.create_run':(obj({'run_id':TEXT,'channel':CHANNEL}),obj({'setup':FREE}),dict(run_id='run_1',channel=1),['touchscreen'],'device_command','agent',
                       'Results > Create Run：以之前运行的参数在通道1或2建立新运行'),
 'results.delete':(obj({'run_ids':dict(type='array',items=TEXT,minItems=1,maxItems=500)}),obj({'deleted':integer(0,500)}),dict(run_ids=['run_1']),['touchscreen'],'device_command','agent',
                   'Data Administration > Result Administration：删除结果（不可恢复；须System Owner）'),
 'owner.login':(obj({'user':STR(),'password':STR(64)}),obj({'unlocked':BOOL}),dict(user=OWNER,password=OWNER_PASSWORD),['touchscreen'],'device_command','agent',
                '进入Data Administration、System Settings、Maintenance前以System Owner权限的用户登录（首次System Owner/1234）'),
 'owner.logout':(obj({}),obj({'unlocked':BOOL}),{},['touchscreen'],'device_command','agent','离开管理视图（锁回）'),
 'columns.save':(obj({'entry':FREE,'original':NUL(STR(40))},optional=('original',)),obj({'name':TEXT}),dict(entry=dict(name='My column',cv_ml=20.,default_flow_ml_min=20.,max_pressure_bar=10.)),['touchscreen'],'device_command','agent',
                 'Column Administration：新增或修改用户自定义柱类型（预置Biotage柱不能修改，只能隐藏）'),
 'columns.delete':(obj({'name':STR()}),obj({'name':TEXT}),dict(name='My column'),['touchscreen'],'device_command','agent','Column Administration：删除用户自定义柱类型'),
 'solvents.save':(obj({'entry':FREE,'original':NUL(STR(40))},optional=('original',)),obj({'name':TEXT}),dict(entry=dict(name='MTBE',strength=.48,max_aspiration_ml_min=420.)),['touchscreen'],'device_command','agent',
                  'Solvent Administration：新增或修改用户自定义溶剂（强度0–1.1、最大吸液速率；预置溶剂只能隐藏）'),
 'solvents.delete':(obj({'name':STR()}),obj({'name':TEXT}),dict(name='MTBE'),['touchscreen'],'device_command','agent','Solvent Administration：删除用户自定义溶剂'),
 'racks.set_volume':(obj({'name':STR(),'vessel_volume_ml':NUL(number('mL',.1,480))}),obj({'name':TEXT,'vessel_volume_ml':number('mL')}),
                     dict(name='48x13x100 mm (9 mL)',vessel_volume_ml=8.),['touchscreen'],'device_command','agent','Rack Administration：只能把容器体积调低；null为Reset'),
 'users.save':(obj({'entry':FREE,'original':NUL(STR(40))},optional=('original',)),obj({'name':TEXT}),dict(entry=dict(name='Chemist 1',privilege='Chemist')),['touchscreen'],'device_command','agent',
               'User Administration：新增或修改用户（可见、名称、System Owner密码、权限、邮箱、自动发送报告、邮件通知）'),
 'users.delete':(obj({'name':STR()}),obj({'name':TEXT}),dict(name='Chemist 1'),['touchscreen'],'device_command','agent','User Administration：删除用户（报告保留但不再显示用户名）'),
 'settings.set':(obj({'changes':FREE}),obj({'settings':FREE,'restart_required':BOOL}),dict(changes=dict(pressure_unit='psi')),['touchscreen'],'device_command','agent',
                 'System Settings：系统（日期、时间须重启生效，语言、压力单位、报警、灯条、鼠标、RFID）、纯化（监控、谷值分馏、手动UV归零、自动延长、确认清洗、基线校正、开始收集、管线/检测器冲洗）、可选检测器'),
 'database.backup':(obj({}),obj({'file':TEXT}),{},['touchscreen','usb_0'],'device_command','agent','Maintenance > Back Up Database：把结果、溶剂、柱类型与用户备份到U盘（之后须重启）'),
 'database.restore':(obj({'file':TEXT}),obj({'file':TEXT}),dict(file='selekt_backup_1.bak'),['touchscreen','usb_0'],'device_command','agent','Maintenance > Restore Database：从U盘备份恢复（之后须重启）'),
 'logs.export':(obj({}),obj({'file':TEXT}),{},['touchscreen','usb_0'],'device_command','agent','Maintenance > Export Logs：系统日志打包导出到U盘\\biotage\\selekt\\logs'),
 'uv.calibrate':(obj({}),obj({'offset_au':dict(type='array',items=number('1')),'gain':number('1')}),{},['uv_cell','touchscreen'],'device_command','agent',
                 'Maintenance > Calibrate UV Detector：流通池充满溶剂时测零点并用内部参比测增益，保存校正并用于读数'),
 'sensor.configure_error':(obj({'uv_offset_au':dict(type='array',items=number('1',-1,1),minItems=2,maxItems=2),'uv_gain':number('1',-.5,.5)}),obj({'configured':BOOL}),
                           dict(uv_offset_au=[0.,0.],uv_gain=0.),['uv_cell'],'configuration','evaluator','评测者：UV检测器系统误差（零点、增益），默认0（理想）'),
 'system.shutdown':(obj({}),obj({'shutdown':BOOL}),{},['touchscreen','power_button'],'device_command','agent','Menu > Shut Down > Yes：有序关机（不在处理时），屏上提示可以关电源后用左侧电源开关断电')}
TCAPS['purge.start']=(obj({'channel':CHANNEL}),obj({'phase':PHASE,'operation_id':TEXT}),dict(channel=0),['selector_valve','touchscreen'],'device_command','agent',
                      'Flushes and Purge > Purge：选择通道（及其柱类型）并释放该柱剩余压力（与pressure.release相同的卸压过程）')
RACKID=dict(type='string',enum=['A','B','C'])
TCAPS['rack.reinsert']=(obj({'rack':RACKID,'vessels_emptied':BOOL}),obj({'rack':TEXT,'cleared':BOOL}),dict(rack='A',vessels_emptied=True),['tray'],'robot_skill','agent',
                        '把管架A/B/C从收集床取下再放回（运行中须先暂停）：vessels_emptied为真时其中各管的馏分离开仪器（计入已收集）；RFID读卡器开启时，只被之前运行用过的管架'
                        '在软件中自动清空，被当前运行用过的管架与读卡器关闭时的所有管架须在Rack面板按Empty（手册p.15-16表1、表3）')
TCAPS['rack.empty']=(obj({'rack':RACKID}),obj({'rack':TEXT}),dict(rack='A'),['touchscreen','tray'],'device_command','agent',
                     'Rack面板Empty：把该管架的所有容器在软件中标为空（操作者已倒空或换上空管；运行中须先暂停，手册p.15-16）')
RFID_TYPE='48x13x100 mm (9 mL)'                                                      # the rack type the RFID tags of the host racks report (A, B, C of 48 tubes)
TERMINAL_CAPS=list(TCAPS)


def _default_setup():
    return dict(user=None,run_name='',comments='',sample_mass_g=None,column_type=None,column_id=None,flow_ml_min=None,unit='CV',
                segments=[dict(start_pct=5.,end_pct=5.,length_cv=1.),dict(start_pct=5.,end_pct=50.,length_cv=10.),dict(start_pct=50.,end_pct=50.,length_cv=2.)],
                solvent_a=None,solvent_b=None,modifier=None,modifier_pct=0.,rack_type=None,equilibration=True,uv1_use='Collect',uv2_use='Collect',uv1_nm=254.,
                uv2_nm=280.,threshold_mau=20.,collect_all=False,max_fraction_ml=None,racks=['A','B','C'],tlc=None)
# a new run starts from a 5 % -> 50 % B linear gradient over 10 CV with 1 CV / 2 CV holds, UV1 254 nm, UV2 280 nm, threshold 20 mAU (assumption)


def _fresh_terminal():
    return dict(setup=[_default_setup(),_default_setup()],pending=None,paused=False,pause_reason=None,hold=False,held_s=0.,awaiting=None,extended=False,
                cleanup=[],flush=None,uv_zero=[0.,0.],uv_raw=[0.,0.],uv_err=dict(offset=[0.,0.],gain=0.),uv_cal=dict(offset=[0.,0.],gain=0.),
                inlets=[dict(solvent=None,capacity_ml=None,level_ml=None,ref_m3=None) for _ in range(4)],waste=dict(capacity_ml=None,level_ml=None,ref_m3=None),
                notices=[],runs=[],run_seq=0,columns=copy.deepcopy(COLUMN_TYPES),solvents=copy.deepcopy(SOLVENTS),
                racks={n:dict(name=n,positions=p,vessel_volume_ml=v,default_ml=v) for n,(p,v) in RACK_TYPES.items()},
                users={OWNER:dict(name=OWNER,visible=True,password=OWNER_PASSWORD,privilege='System Owner',email=None,auto_send_report=False,email_notifications=False)},
                owner=False,settings=copy.deepcopy(SETTINGS_DEFAULT),pending_settings={},usb=[],shutdown=False,logs=0,allowed=None,valley=dict(prev=None,low=None),after_purge=None,
                clock_offset_s=0.,vessel_used=[False]*144)

for k,s_,zh,comp in [('run.setups',arr(FREE,2),'两个通道的运行设定（Information、Column、Gradient、Collection、Rack面板）','touchscreen'),
                     ('run.issues',arr(TEXT),'警告按钮列出的缺失或错误参数（为空时开始键可用）','touchscreen'),('run.estimate',FREE,'当前通道梯度的溶剂与废液估计用量（mL）','touchscreen'),
                     ('run.pending_sample_load',NUL(CHANNEL),'等待上样的通道（Sample Load对话框打开）','touchscreen'),('run.paused',BOOL,'运行暂停（泵停、收集臂回原位）','touchscreen'),
                     ('run.pause_reason',NUL(TEXT),'暂停原因（用户、补溶剂、倒废液）','touchscreen'),('run.hold',BOOL,'Hold等度段进行中','touchscreen'),
                     ('run.awaiting',NUL(TEXT),'等待用户确认（gradient_completed：梯度完成对话框）','touchscreen'),
                     ('solvents.inlets',arr(FREE,4),'S1–S4指定的溶剂、容量与监控液量（mL）','solvent_0'),('waste.level',FREE,'废液瓶容量与监控液量（mL）','waste_port'),
                     ('notices',arr(TEXT),'屏上通知（溶剂低于20 %、废液高于85 %）','touchscreen'),
                     ('rack.vessels_used',arr(integer(0,48),3),'软件中管架A/B/C已用（不能再收集）的容器数（Empty或RFID自动清空后归零）','tray'),
                     ('rack.type',NUL(TEXT),'收集床上管架的类型：RFID读卡器开启时由标签识别，关闭时为Rack面板手选（未选为空）','tray'),('uv.zero_au',arr(number('1'),2),'手动UV归零的偏移（AU）','uv_cell'),
                     ('uv.calibration',FREE,'UV检测器校准保存的零点与增益','uv_cell'),('results.list',arr(FREE),'结果列表（运行名、用户、通道、柱、开始时间、状态、收藏、注释、报告设定）','touchscreen'),
                     ('library.columns',arr(FREE),'柱类型库（预置Biotage与用户自定义）','touchscreen'),('library.solvents',arr(FREE),'溶剂库','touchscreen'),
                     ('library.racks',arr(FREE),'管架类型与容器体积','touchscreen'),('library.users',arr(FREE),'用户账户（密码只显示是否设置）','touchscreen'),
                     ('settings',FREE,'系统设置（系统、纯化、可选检测器）','touchscreen'),('settings.pending',FREE,'重启后生效的日期与时间','touchscreen'),
                     ('owner.unlocked',BOOL,'管理视图已以System Owner解锁','touchscreen'),('usb.files',arr(TEXT),'U盘上由仪器写入的文件','usb_0'),
                     ('system.shutdown',BOOL,'已有序关机（可关电源开关）','power_button'),('system.restart_required',BOOL,'备份/恢复数据库后须重启','touchscreen'),
                     ('system.datetime',TEXT,'仪器本地日期时间（ISO）','touchscreen'),('flush.state',NUL(FREE),'进行中的冲洗（类型、路径、剩余体积mL）','pump'),
                     ('status.light',NUL(dict(type='string',enum=['white','orange','blue','green','red'])),'触屏下方灯条颜色（关闭为空）','status_light')]:
 slot(k,s_,'1',zh,component=comp)
slot('truth.uv_error',FREE,'1','评测者设定的UV零点与增益误差',o='process_truth',vis='evaluator',component='uv_cell')


# ---------------------------------------------------------------- front panel: engine, spec and hooks (gradient table edits, TLC to gradient, views)
def _panel_engine():
 try:
  from . import _panel_ui as engine                                       # imported as asset_runtime.biotage_selekt (tests, Matterix bridge)
  return engine
 except ImportError:                                                       # loaded from its file path
  if '_chemsim_panel_ui' not in sys.modules:
   spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
import os as _os
if _os.environ.get('SELEKT_PANEL_BUILD'):                                  # the spec builder imports this module before the spec exists
 PANEL_SPEC=dict(spec_version=_panel_ui.SPEC_VERSION,inputs={},displays={'screen':dict(kind='touch_tft',size=[1024,768])},screens={'off':dict(style='static')})
else:
 PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(TCAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC)
PANEL_GESTURES=[k for k in PANEL_CAPS if k not in ('panel.capture','panel.frame')]


def _ch(panel):
 return int(panel.ui['vars'].get('ch') or 0)


def _setup(panel):
 return panel._state()['run.setups'][_ch(panel)]


def _segments_with(panel,ctx,s):
 """Gradient table + / - and row edits (manual p.9): a new segment after the selected one (or at the end) continues from its end mix; '-' deletes the
 selected segment; 'set' writes the Segment dialog's values (length typed in the table unit)."""
 segs=[dict(g) for g in _setup(panel)['segments']];k=panel.ui['vars'].get('seg');op=s['op']
 if op=='add':
  i=len(segs)-1 if k is None else min(int(k),len(segs)-1);e=segs[i]['end_pct'];segs.insert(i+1,dict(start_pct=e,end_pct=e,length_cv=1.))
 elif op=='del':
  if k is not None and len(segs)>1 and int(k)<len(segs):del segs[int(k)]
 else:
  b=(panel.ui['forms'].get('f_seg') or {}).get('buffer') or ctx.get('buffer') or {}
  segs[int(k)]=dict(start_pct=float(b['start_pct']),end_pct=float(b['end_pct']),length_cv=_to_cv(panel,float(b['length'])))
 return segs


def _per_cv(panel):
 """Table unit per CV: 1 (CV), the column volume (mL) or the CV time at the run flow rate (s, shown as mm:ss)."""
 st=_setup(panel);lib={c['name']:c for c in panel._state()['library.columns']};cv=lib.get(st['column_type'],{}).get('cv_ml') or 1.
 return {'CV':1.,'mL':cv,'mm:ss':cv/(st['flow_ml_min'] or 1)*60.}[st['unit']]


def _to_cv(panel,x):
 return max(.01,round(x/_per_cv(panel),4))


def _length_text(panel,cv):
 x=cv*_per_cv(panel);u=_setup(panel)['unit']
 return f'{int(round(x))//60:02d}:{int(round(x))%60:02d}' if u=='mm:ss' else f'{x:.1f} {u}'


def _src_seg_rows(panel,ctx,s):
 return [dict(n=i,start=f'{g["start_pct"]:.0f} %',end=f'{g["end_pct"]:.0f} %',length=_length_text(panel,g['length_cv'])) for i,g in enumerate(_setup(panel)['segments'])]


def _src_seg_form(panel,ctx,s):
 g=_setup(panel)['segments'][int(panel.ui['vars'].get('seg') or 0)]
 return dict(start_pct=g['start_pct'],end_pct=g['end_pct'],length=round(g['length_cv']*_per_cv(panel),2))      # the engine's 'at' picks the field


def _src_tlc(panel,ctx,s):
 """TLC to Gradient (manual p.9-10): one plate gives a linear gradient, two plates a step gradient.  The Biotage algorithm is not published:
 assumption - the product elutes near 1 / Rf CV at the TLC strength, so the gradient starts at a quarter of the TLC strength and reaches twice it
 over 10 CV (one plate) or holds each plate's strength for 1 / Rf + 2 CV (two plates); Rf Impurity 1 is required."""
 b=(panel.ui['forms'].get('f_tlc') or {}).get('buffer') or {}
 p1=float(b.get('strong1') or 0.);r1=float(b.get('rf1') or .3)
 if not b.get('plate2'):
  lo=round(p1/4,1);hi=min(100.,round(2*p1,1))
  return [dict(start_pct=lo,end_pct=lo,length_cv=1.),dict(start_pct=lo,end_pct=hi,length_cv=10.),dict(start_pct=hi,end_pct=hi,length_cv=2.)]
 p2=float(b.get('strong2') or p1);r2=float(b.get('rf2') or r1)
 return [dict(start_pct=p1,end_pct=p1,length_cv=round(1/max(r1,.05)+2,1)),dict(start_pct=p2,end_pct=p2,length_cv=round(1/max(r2,.05)+2,1))]


def _src_view(panel,ctx,s):
 h=panel.host;kind=s['view']
 if kind=='chrom':                                                          # chromatogram of the current run (mAU) or of a result (Analysis)
  rid=panel.ui['vars'].get('res') if s.get('result') else None
  if s.get('result'):r=next((x for x in h.term['runs'] if x['run_id']==rid),None)
  else:r=next((x for x in reversed(h.term['runs']) if x['channel']==_ch(panel)+1),None)    # the latest run of the channel shown
  if r is None:return []
  hist=h.history[r['history_from']:]
  if r is not (h.term['runs'][-1] if h.term['runs'] else None) or r.get('final'):hist=[x for x in hist if x['time_s']<=r.get('end_time_s',math.inf)]
  key='uv1' if s['signal']==1 else 'uv2'
  return [[round(float(x['time_s']-r['start_time_s']),1),round(float(x[key])*1000.,2)] for x in hist if x['channel']==r['channel']-1]
 if kind=='gradient':                                                       # programmed gradient (% strong solvent over the gradient seconds)
  m=h.methods[h.selected]
  if m is None:
   try:return [[n['time_s'],n['strong_fraction']*100.] for n in h._gradient_times(_ch(panel))]
   except Exception:return []
  return [[n['time_s'],n['strong_fraction']*100.] for n in m['gradient']]
 if kind=='pressure':                                                       # pressure of the channel shown in the selected unit
  p=float(h.pressure[_ch(panel)])/1e5
  return f'{p*14.5038:.0f} psi' if h.term['settings']['pressure_unit']=='psi' else f'{p:.1f} bar'
 if kind=='mix':                                                            # current solvent mix under the chromatogram (manual p.14)
  st_=_setup(panel);x=h._values()['gradient.mixer_fraction'] if h.selected==_ch(panel) and h.phase=='running' else st_['segments'][0]['start_pct']/100
  mod=h._modifier(_ch(panel));fm=mod['fraction'] if mod else 0.
  if h.selected==_ch(panel) and h.phase=='running' and mod:x=x/(1-fm) if fm<1 else 0.     # the A / B shares exclude the modifier (manual p.14)
  out=f'A: {(1-x)*(1-fm)*100:.0f} % {st_["solvent_a"] or "None"}    B: {x*(1-fm)*100:.0f} % {st_["solvent_b"] or "None"}'
  return out+(f'    M: {fm*100:g} % {st_["modifier"]}' if mod else '')
 if kind=='flow':
  return f'{h.flow*6e7:.0f} mL/min' if h.selected==_ch(panel) else '0 mL/min'
 if kind=='elapsed':
  t=int(h.elapsed_s) if h.selected==_ch(panel) else 0;return f'{t//60:02d}:{t%60:02d}'
 if kind=='left':
  m=h.methods[h.selected]
  if m is None or h.phase!='running' or h.selected!=_ch(panel):return '--:--'
  t=int(max(0.,m['gradient'][-1]['time_s']-h.gradient_s[h.selected]));return f'{t//60:02d}:{t%60:02d}'
 if kind=='span':                                                           # x window of the chromatogram: the whole run so far, at least the gradient
  pts=_src_view(panel,ctx,dict(s,view='chrom',signal=1));m=h.methods[_ch(panel)]
  return max(60.,pts[-1][0] if pts else 0.,(m['gradient'][-1]['time_s'] if m else 0.))
 if kind=='process':                                                        # the operation in progress (Flushes and Purge view)
  return (STAGE_TEXT.get(h.stage,h.stage)+' in progress') if h.phase=='running' else ''
 if kind=='estimate':
  e=h._estimate(_ch(panel));return '\n'.join([f'S{k+1}: {v:.0f} mL' for k,v in enumerate(e['inlets_ml']) if v>0]+[f'Waste: {e["waste_ml"]:.0f} mL'])
 raise KeyError(kind)


STAGE_TEXT={'conversion':'Conversion','equilibration':'Equilibration','sample_load':'Sample Load','gradient':'Gradient','gradient_completed':'Gradient Run Completed','purging':'Purge',
            'line_flush':'Line Flush','detector_flush':'Detector Flush','finished_residual_solute':'Finished','aborted':'Finished','prime':'Prime',
            'flush':'Flush','air_flush':'Air Flush','flushed':'Finished','standby':''}                   # status in the top pane (manual p.14)


def _src_cur(panel,ctx,s):
 """A run-setup value of the channel shown (var ch)."""
 return _setup(panel)                                                         # the engine's 'at' picks the field


def _dig_path(v,path):
 for k in ([] if path is None else str(path).split('.')):
  v=v.get(k) if isinstance(v,dict) else None
 return v


def _src_chan(panel,ctx,s):
 """State of the channel shown: busy (its run or flush is processing), stage text, waiting for Sample Load, paused, Gradient Run Completed."""
 h=panel.host;ch=_ch(panel);mine=h.selected==ch;run=h.phase=='running' and mine
 st_=STAGE_TEXT.get(h.stage,h.stage) if mine else ''
 if mine and h.phase=='failed':st_='Failed'
 out=dict(busy=run,stage=st_,pending=h.term['pending']==ch,paused=run and h.term['paused'],reason=h.term['pause_reason'] if run else None,
          completed=run and h.term['awaiting']=='gradient_completed',gradient=run and h.stage=='gradient',equil=run and h.stage=='equilibration',
          idle=not (h.phase=='running'),issues=h._setup_issues(ch),issues_text='\n'.join(h._setup_issues(ch)) or 'All run parameters are set.',
          notices_text='   '.join(h._notices()),start_ok=not h._setup_issues(ch) and h.phase!='running' and not h.done[ch],
          done=h.done[ch],other_busy=h.phase=='running' and not mine)
 return out


def _src_results(panel,ctx,s):
 """Results view list (manual p.19): favorites first, filtered on user, chemistry type, run date and the scanned column ID."""
 v=panel.ui['vars'];out=[]
 for r in panel._state()['results.list']:
  if v.get('f_user') and r['user']!=v['f_user']:continue
  if v.get('f_chem') and r['chemistry']!=v['f_chem']:continue
  if v.get('f_date') and not str(r['started']).startswith(v['f_date']):continue
  if v.get('f_col') and r['column_id']!=v['f_col']:continue
  out.append(dict(r,star='*' if r['favorite'] else ' ',when=str(r['started']).replace('T',' ')[:16]))
 return out


def _tube_name(j):
 return f'{"ABC"[j//RACK_SIZE]}{j%RACK_SIZE+1}'


def _src_rack(panel,ctx,s):
 """Rack panel text: assigned racks, used vessels per rack, the first vessel the run will use (or uses now)."""
 h=panel.host;ch=_ch(panel);st_=_setup(panel)
 if not h.rack:return 'No rack on the collection bed'
 used=np.array(h.term['vessel_used']);n=h.rack['positions'];lines=[]
 rt_=h._rack_type(ch);lines.append('Type: '+(rt_+(' (RFID)' if h.term['settings']['rfid'] else '') if rt_ else 'select the rack type'))
 for k,r in enumerate('ABC'):
  lo=k*RACK_SIZE
  if lo>=n:break
  hi=min(n,lo+RACK_SIZE);lines.append(f'{"[x]" if r in st_["racks"] else "[ ]"} {r}: {int(used[lo:hi].sum())}/{hi-lo} used')
 nxt=h.tube if (h.phase=='running' and h.selected==ch and h.term.get('allowed') is not None) else (h._allowed_tubes(ch) or [None])[0]
 lines.append('Next vessel: '+('-' if nxt is None or nxt>=n else _tube_name(nxt)))
 vol=h._rack_vessel_ml(ch)
 lines.append(f'Max fraction: {st_["max_fraction_ml"] or vol:.1f} mL')
 return '\n'.join(lines)


def _src_fractions(panel,ctx,s):
 """Analysis view: the vessels of the selected result with their volume."""
 h=panel.host;rid=panel.ui['vars'].get('res');r=next((x for x in h.term['runs'] if x['run_id']==rid),None)
 if r is None:return 'No result selected'
 if not r['tubes']:return 'No fractions collected'
 return 'Fractions: '+', '.join(f'{_tube_name(j)} {float(h.fractionfluid[j].sum())/ML:.1f} mL' for j in r['tubes'][:24])+(' ...' if len(r['tubes'])>24 else '')


def _src_backups(panel,ctx,s):
 """Backup files on the USB memory device (\\biotage\\selekt\\backup)."""
 out=[dict(file=f.rsplit('/',1)[-1]) for f in panel._state()['usb.files'] if f.startswith('biotage/selekt/backup/')]
 return out


PANEL_HOOKS={'backups':_src_backups,'segments':_segments_with,'seg_rows':_src_seg_rows,'seg_form':_src_seg_form,'tlc':_src_tlc,'view':_src_view,'cur':_src_cur,'chan':_src_chan,
             'results':_src_results,'rack':_src_rack,'fractions':_src_fractions}

class BiotageSelektRuntime:
 def __init__(self,instance_id='selekt_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.columns=[None,None];self.samples=[None,None];self.methods=[None,None];self.rack=None;self.powered=False;self.selected=0;self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.gradient_s=np.zeros(2);self.done=[False,False];self.pressure=np.zeros(2);self.flow=0.;self.tray=0.;self.x=0.;self.y=.33;self.moving=False;self.fraction=0;self.tube=0;self.uv=np.zeros(2);self.operation_id=None;self._operation_count=0;self.history=[];self.next_record=0.;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.ids=list(self.templates);self.ns=len(self.ids);self.reservoir=np.zeros(4);self.mixer=np.zeros(4);self.detfluid=np.zeros(4);self.colfluid=np.zeros((2,4,N));self.fractionfluid=np.zeros((144,4));self.wastefluid=np.zeros(4);self.initialfluid=np.zeros(4);self.colmol=np.zeros((2,self.ns,N));self.detmol=np.zeros(self.ns);self.fractionmol=np.zeros((144,self.ns));self.wastemol=np.zeros(self.ns);self.initialmol=np.zeros(self.ns);self.k0=np.zeros((2,self.ns));self.slope=np.zeros((2,self.ns));self.epsilon=np.zeros((2,self.ns));self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
  self._caller=None                                                       # session principal while a session call runs (panel gestures act as that caller)
  self.modw=np.zeros((2,4))                                               # elution weight of a modifier inlet per column (strong solvent = 1; manual p.9 M/Modifier)
  self.removedfluid=np.zeros(4);self.removedmol=np.zeros(self.ns)          # fractions of racks taken off the bed with their vessels (rack.reinsert, vessels emptied)
  self.term=_fresh_terminal();self.power_epoch=0;self.powered_since=0.;self.panel_capture_dir=None;self.panel=_panel_ui.PanelUI(PANEL_SPEC,self,hooks=PANEL_HOOKS)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BiotageSelektSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _target_phi(self):
  m=self.methods[self.selected]
  return float(np.interp(self.gradient_s[self.selected],[r['time_s'] for r in m['gradient']],[r['strong_fraction'] for r in m['gradient']])) if m else 0.
 def _values(self):
  i=self.selected;m=self.methods[i];v={'power.enabled':self.powered,'plant.configured':bool(self.plant),'rack.configured':bool(self.rack),'channel.selected':i,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'cycle.gradient_s':float(self.gradient_s[i]),'flow.target_m3_s':m['flow_m3_s'] if m else 0.,'flow.actual_m3_s':self.flow,'gradient.target_fraction':self._target_phi(),'gradient.mixer_fraction':float(self.mixer[self.columns[i]['solvent_pair'][1]]/self.plant['mixer_volume_m3']) if self.plant and self.columns[i] else 0.,'pressure.gauge_pa':float(self.pressure[i]),'uv.absorbance_1':float(self.uv[0]),'uv.absorbance_2':float(self.uv[1]),'collection.fraction_number':self.fraction,'collection.positions_used':int(np.count_nonzero(self.fractionfluid.sum(axis=1)>0)),'collector.moving':self.moving,'tray.extension_m':self.tray,'collector.x_m':self.x,'collector.y_m':self.y,'truth.initial_amount_mol':float(self.initialmol.sum()),'truth.column_amount_mol':float(self.colmol.sum()),'truth.detector_amount_mol':float(self.detmol.sum()),'truth.fractions_amount_mol':float(self.fractionmol.sum()+self.removedmol.sum()),'truth.waste_amount_mol':float(self.wastemol.sum())}
  v.update(self._terminal_values())
  for k in range(2):v.update({f'channel.c{k}.column_ready':bool(self.columns[k]),f'channel.c{k}.sample_loaded':bool(self.samples[k]),f'channel.c{k}.method_ready':bool(self.methods[k]),f'channel.c{k}.pressure_pa':float(self.pressure[k])})
  holdup=self.mixer+self.detfluid+self.colfluid.sum(axis=(0,2))
  for k in range(4):
   for name,a in [('initial',self.initialfluid),('reservoir',self.reservoir),('holdup',holdup),('fractions',self.fractionfluid.sum(axis=0)+self.removedfluid),('waste',self.wastefluid)]:v[f'truth.solvent_{k}_{name}_m3']=float(a[k])
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),plant=self.plant,columns=self.columns,samples=self.samples,methods=self.methods,rack=self.rack,clock=self.time_s,revision=self.revision,products=self._products(),history=self.history))
 def _products(self):return dict(substance_ids=self.ids,initial_amount_mol=self.initialmol.tolist(),column_amount_mol=self.colmol.sum(axis=(0,2)).tolist(),detector_amount_mol=self.detmol.tolist(),waste_amount_mol=self.wastemol.tolist(),fraction_amount_mol=self.fractionmol.tolist(),fraction_volume_m3=self.fractionfluid.sum(axis=1).tolist(),waste_volume_m3=float(self.wastefluid.sum()),measured_Selekt_recovery=None,evidence='experimental UV reference with assumed retention andsolvent transfer;finite inventories not measured Selekt recovery',template_source=TEMPLATE_REL)
 def _epsilon(self,sid,wavelength):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  r=self.templates[sid];x=r['values']['wavelength_m'];y=r['values']['molar_decadic_absorptivity_m2_mol']
  if wavelength<x[0]-1e-15 or wavelength>x[-1]+1e-15:raise ActionRejected('REFERENCE_RANGE')
  return float(np.interp(wavelength,x,y))
 def _can_service(self):return self.phase!='running' and max(self.pressure)<1000
 def invoke(self,capability,**a):
  if capability in TCAPS:return self._tinvoke(capability,a)
  if capability in PANEL_CAPS:return self._pinvoke(capability,a)
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ['power.set','acquisition.stop']:raise ActionRejected('BUSY')
  i=a.get('channel',self.selected)
  if capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['solvent_compatibility_confirmed','fume_hood_confirmed','unused_channel_connected','spectral_transfer_assumption_accepted']):raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.reservoir=np.array(a['solvent_volumes_m3']);self.mixer[0]=a['mixer_volume_m3'];self.detfluid[0]=a['detector_volume_m3'];self.initialfluid=self.reservoir+self.mixer+self.detfluid;out=dict(configured=True)
  elif capability=='column.configure':
   if not self.plant or self.columns[i] or not self._can_service() or not a['column_compatibility_confirmed'] or a['solvent_pair'] not in ([0,1],[2,3]):raise ActionRejected('PRECONDITION_FAILED')
   self.columns[i]=copy.deepcopy(a);p=a['solvent_pair'];v=a['void_volume_m3']/N;phi=a['conditioned_strong_fraction'];self.colfluid[i,p[0]]=v*(1-phi);self.colfluid[i,p[1]]=v*phi;self.initialfluid+=self.colfluid[i].sum(axis=1);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.columns[i] or self.samples[i] or self.methods[i] or not self._can_service() or not a['dilute_linear_load_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   if sum(r['amount_mol'] for r in a['species'])>self.columns[i]['max_sample_amount_mol'] or len({r['substance_id'] for r in a['species']})!=len(a['species']):raise ActionRejected('LOAD_ENVELOPE')
   if any(r['substance_id'] not in self.templates for r in a['species']):raise ActionRejected('UNKNOWN_REFERENCE')
   for r in a['species']:
    j=self.ids.index(r['substance_id']);self.colmol[i,j,0]=r['amount_mol'];self.initialmol[j]+=r['amount_mol'];self.k0[i,j]=r['k0'];self.slope[i,j]=r['solvent_strength_slope']
   self.samples[i]=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if not self.samples[i] or self.gradient_s[i]>0 or not self._can_service():raise ActionRejected('PRECONDITION_FAILED')
   times=[r['time_s'] for r in a['gradient']];mlmin=a['flow_m3_s']*60e6
   if abs(mlmin-round(mlmin))>1e-7 or times[0]!=0. or any(y<=x for x,y in zip(times,times[1:])) or a['timeout_s']<=times[-1]:raise ActionRejected('METHOD_ENVELOPE')
   for sid,n in zip(self.ids,self.initialmol):
    if n>0:
     for wl in a['wavelengths_m']:self._epsilon(sid,wl)
   self.methods[i]=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='rack.configure':
   if self.rack or self.phase=='running' or self.tray<.1:raise ActionRejected('PRECONDITION_FAILED')
   self.rack=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='tray.set_extension':
   if not self._can_service():raise ActionRejected('PRECONDITION_FAILED')
   self.tray=a['extension_m'];out=a.copy()
  elif capability=='channel.select':
   if not self._can_service() or self.done[i]:raise ActionRejected('PRECONDITION_FAILED')
   self.selected=i;self.phase='idle' if self.gradient_s[i]==0 else 'cancelled';self.stage='standby';self.elapsed_s=0.;self.fault='';out=a.copy()
  elif capability=='power.set':
   if a['enabled'] and not self.powered:self.power_epoch+=1;self._power_up()     # start-up (terminal: pending date / time, locks)
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.flow=0.;self.fraction=0
   if not self.powered:self.term.update(flush=None,cleanup=[],paused=False,pause_reason=None,hold=False,awaiting=None,pending=None)
   out=a.copy()
  elif capability in ('acquisition.start','pressure.release'):
   if not self.powered or not self.plant or self.tray>0 or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   if capability=='acquisition.start':
    if not self.methods[i] or not self.rack or self.done[i] or self.phase not in ('idle','cancelled') or self.gradient_s[i]>=self.methods[i]['gradient'][-1]['time_s']:raise ActionRejected('PRECONDITION_FAILED')
    eps=np.zeros_like(self.epsilon)
    for j,n in enumerate(self.initialmol):
     if n>0:
      for w,wl in enumerate(self.methods[i]['wavelengths_m']):eps[w,j]=self._epsilon(self.ids[j],wl)
    self.epsilon=eps;self.stage='gradient'
   else:
    if self.pressure[i]<=1.:raise ActionRejected('PRECONDITION_FAILED')
    self.stage='purging'
   self._freeze_runs();self.phase='running';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
   self.term.update(flush=None,cleanup=[],allowed=None,valley=dict(prev=None,low=None))
   if capability=='acquisition.start':self._record_run();self.term.update(extended=False,hold=False,held_s=0.,awaiting=None,paused=False,pause_reason=None)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.flow=0.;self.fraction=0
   self.term.update(flush=None,cleanup=[],paused=False,pause_reason=None,hold=False,awaiting=None)
   out=dict(phase=self.phase)
  elif capability=='chromatography.measure':out=dict(phase=self.phase,stage=self.stage,fault=self.fault,channel=self.selected,pressure_gauge_pa=float(self.pressure[self.selected]),flow_m3_s=self.flow,strong_fraction=self._values()['gradient.mixer_fraction'],uv_absorbance=self.uv.tolist(),fraction_number=self.fraction,evidence='conditional transport and BeerLambert using measured reference absorption;not calibrated Selekt chromatogram',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=self._products()
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='derived chromatographic observations, not measured instrument traces')
  else:
   eps=self._epsilon(a['substance_id'],a['wavelength_m']);r=self.templates[a['substance_id']];out=dict(substance_id=a['substance_id'],wavelength_m=a['wavelength_m'],epsilon_m2_mol=eps,solvent_description=r['conditions']['solvent_description'],evidence='measured_literature scaled spectrum with linear wavelength interpolation,not chromatographic solvent calibration',template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)

 # ---------------------------------------------------------------- terminal layer (touch-screen software)
 def _tinvoke(self,capability,a):
  validate(TCAPS[capability][0],a)
  out=getattr(self,'_t_'+capability.replace('.','_'))(**a)
  validate(TCAPS[capability][1],out)
  self.revision+=1
  return copy.deepcopy(out)
 def _need_on(self):
  if not self.powered or self.term['shutdown']:raise ActionRejected('POWER_OFF')
 def _need_owner(self):
  self._need_on()
  if not self.term['owner']:raise ActionRejected('OWNER_REQUIRED')
 def _need_idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _clock(self):
  import datetime
  return (datetime.datetime.fromisoformat('2026-01-01T00:00:00')+datetime.timedelta(seconds=round(self.time_s+self.term['clock_offset_s']))).isoformat()
 def _column_type(self,name):
  c=self.term['columns'].get(name)
  if c is None:raise ActionRejected('UNKNOWN_COLUMN')
  return c
 def _inlet_of(self,solvent):
  return next((k for k,x in enumerate(self.term['inlets']) if solvent is not None and x['solvent']==solvent),None)
 def _setup_issues(self,ch):
  """The warning button (manual p.8, p.13): what keeps the start button disabled."""
  s=self.term['setup'][ch];out=[]
  if s['user'] is None:out.append('Select a user')
  if s['column_type'] is None:out.append('Select the column type')
  if s['flow_ml_min'] is None:out.append('Enter the flow rate')
  if s['solvent_a'] is None or s['solvent_b'] is None:out.append('Select solvents A and B')
  elif self._inlet_of(s['solvent_a']) is None or self._inlet_of(s['solvent_b']) is None:out.append('Solvent A or B is not assigned to an inlet')
  elif self.columns[ch] and [self._inlet_of(s['solvent_a']),self._inlet_of(s['solvent_b'])]!=list(self.columns[ch]['solvent_pair']):out.append('Solvents A/B are not the inlets plumbed to this column')
  if s.get('modifier') and (s.get('modifier_pct') or 0)>0:
   km=self._inlet_of(s['modifier'])
   if km is None:out.append('The modifier is not assigned to an inlet')
   elif km in (self._inlet_of(s['solvent_a']),self._inlet_of(s['solvent_b'])):out.append('Select a modifier other than solvents A and B')
  if not self.term['settings']['rfid'] and not s.get('rack_type'):out.append('Select the rack type')   # RFID reader off: rack type by hand (manual p.11)
  if not self.columns[ch]:out.append('No column mounted on this channel')
  if not self.rack or not s['racks']:out.append('No collection rack')
  elif not self._allowed_tubes(ch):out.append('No empty vessels in the assigned racks')
  if self.tray>0:out.append('Close the collection tray')
  if s['column_type'] in self.term['columns'] and s['flow_ml_min'] and self._gradient_times(ch)[-1]['time_s']>7200:out.append('Gradient longer than 120 min')
  if s['uv1_use']!='Collect' and s['uv2_use']!='Collect' and not s['collect_all']:out.append('No collection signal')
  for k,need in enumerate(self._estimate(ch)['inlets_ml']):
   lv=self._levels()['inlets_ml'][k]
   if self.term['settings']['solvent_monitoring'] and need>0 and lv is not None and lv<need:out.append(f'Not enough solvent in S{k+1}')
  w=self._levels()['waste_ml']
  if self.term['settings']['waste_monitoring'] and w is not None and self.term['waste']['capacity_ml'] and w+self._estimate(ch)['waste_ml']>self.term['waste']['capacity_ml']:out.append('Not enough waste capacity')
  return out
 def _modifier(self,ch):
  """M/Modifier of the run setup (manual p.9: a fixed percentage of a solvent in the mix): inlet, fraction and elution weight, or None.  The weight
  places the modifier on the A -> B strength scale by the eps0 strengths of Solvent Administration (A = 0, B = 1; linear in eps0: assumption)."""
  s=self.term['setup'][ch];name=s.get('modifier');pct=float(s.get('modifier_pct') or 0.)
  if not name or pct<=0:return None
  k,a,b=self._inlet_of(name),self._inlet_of(s['solvent_a']),self._inlet_of(s['solvent_b'])
  if k is None or a is None or b is None or k in (a,b):return None
  sv=self.term['solvents'];ea,eb,em=(sv[n]['strength'] if n in sv else 0. for n in (s['solvent_a'],s['solvent_b'],name))
  w=(em-ea)/(eb-ea) if abs(eb-ea)>1e-9 else 1.
  return dict(inlet=k,fraction=pct/100.,weight=float(min(10.,max(0.,w))))
 def _set_modifier(self,ch):
  mod=self._modifier(ch);self.modw[ch]=0.
  if mod:self.modw[ch,mod['inlet']]=mod['weight']
  return mod
 def _conversion(self,ch):
  """Automatic conversion between normal and reversed phase (manual p.7, p.14): the previous run was on the other channel with a column for the other
  chemistry, neither run uses more than two solvents (no modifier; solvents C / D are not offered) and the previous strong solvent is still connected:
  the system pumps 100 % of the previous strong solvent, then 100 % of the current strong solvent, via the bypass (volumes and flow: assumption)."""
  t=self.term;s=t['setup'][ch]
  if not t['runs'] or s['column_type'] not in t['columns']:return []
  prev=t['runs'][-1];ps=prev['setup']
  if prev['channel']==ch+1 or prev.get('chemistry','normal')==t['columns'][s['column_type']]['chemistry']:return []
  if any(x.get('modifier') and (x.get('modifier_pct') or 0)>0 for x in (ps,s)):return []
  kp,kc=self._inlet_of(ps.get('solvent_b')),self._inlet_of(s['solvent_b'])
  if kp is None or kc is None:return []
  out=[]
  for k in (kp,kc):
   comp=[0.]*4;comp[k]=1.;out.append(dict(kind='liquid',path=None,comp=comp,q=PRIME_ML_MIN*ML/60.,left=CONVERSION_ML*ML,dest='waste'))
  return out
 def _gradient_times(self,ch):
  """Segment lengths (CV) -> method nodes in seconds at the run flow rate (the column type's CV); a step between segments gets a 0.01 s ramp."""
  s=self.term['setup'][ch];cv=self.term['columns'][s['column_type']]['cv_ml'];q=s['flow_ml_min']/60.;t=0.;nodes=[]
  for g in s['segments']:
   if not nodes or abs(nodes[-1]['strong_fraction']-g['start_pct']/100)>1e-12:nodes.append(dict(time_s=t+(.01 if nodes else 0.),strong_fraction=g['start_pct']/100))
   t+=g['length_cv']*cv/q;nodes.append(dict(time_s=t,strong_fraction=g['end_pct']/100))
  return nodes
 def _estimate(self,ch):
  """Solvents and Waste (manual p.13): estimated consumption of the gradient per inlet and the waste it produces."""
  s=self.term['setup'][ch];out=[0.]*4
  if s['column_type'] in self.term['columns']:
   cv=self.term['columns'][s['column_type']]['cv_ml'];a,b=self._inlet_of(s['solvent_a']),self._inlet_of(s['solvent_b'])
   mod=self._modifier(ch);fm=mod['fraction'] if mod else 0.
   segs=list(s['segments'])
   if s['equilibration']:x=s['segments'][0]['start_pct'];segs.insert(0,dict(start_pct=x,end_pct=x,length_cv=self.term['columns'][s['column_type']]['equil_length_cv']))
   for g in segs:
    v=g['length_cv']*cv;fb=(g['start_pct']+g['end_pct'])/200
    if a is not None:out[a]+=v*(1-fb)*(1-fm)
    if b is not None:out[b]+=v*fb*(1-fm)
    if mod:out[mod['inlet']]+=v*fm
  return dict(inlets_ml=out,waste_ml=sum(out))
 def _levels(self):
  """Solvent and waste monitoring (manual p.2, p.27): entered level minus what the system pumped since then."""
  lv=[]
  for k,x in enumerate(self.term['inlets']):
   lv.append(None if x['level_ml'] is None else x['level_ml']-(x['ref_m3']-float(self.reservoir[k]))/ML)
  w=self.term['waste'];wl=None if w['level_ml'] is None else w['level_ml']+(float(self.wastefluid.sum())-w['ref_m3'])/ML
  return dict(inlets_ml=lv,waste_ml=wl)
 def _notices(self):
  """Level notifications (manual p.27): a solvent below 20 % of its capacity, the waste above 85 %."""
  t=self.term;s=t['settings'];notes=[];lv=self._levels()
  for k,x in enumerate(t['inlets']):
   if s['solvent_monitoring'] and x['capacity_ml'] and lv['inlets_ml'][k] is not None and lv['inlets_ml'][k]/x['capacity_ml']<.2:notes.append(f'S{k+1} {x["solvent"]} below 20 %')
  if s['waste_monitoring'] and t['waste']['capacity_ml'] and lv['waste_ml'] is not None and lv['waste_ml']/t['waste']['capacity_ml']>.85:notes.append('Waste above 85 %')
  return notes
 def _monitor(self):
  t=self.term;s=t['settings'];notes=[];lv=self._levels()
  for k,x in enumerate(t['inlets']):
   if s['solvent_monitoring'] and x['capacity_ml'] and lv['inlets_ml'][k] is not None:
    f=lv['inlets_ml'][k]/x['capacity_ml']
    if f<.2:notes.append(f'S{k+1} {x["solvent"]} below 20 %')
    if f<=.1 and self.phase=='running' and not t['paused']:t['paused']=True;t['pause_reason']=f'Refill solvent S{k+1}'
  if s['waste_monitoring'] and t['waste']['capacity_ml'] and lv['waste_ml'] is not None:
   f=lv['waste_ml']/t['waste']['capacity_ml']
   if f>.85:notes.append('Waste above 85 %')
   if f>=.95 and self.phase=='running' and not t['paused']:t['paused']=True;t['pause_reason']='Empty the waste reservoir'
  t['notices']=notes
 def _uv_raw(self):
  return self.epsilon@self.detmol/self.plant['detector_volume_m3']*self.plant['optical_path_m']*self.plant['uv_response_scale']
 def _uv_unclipped(self,raw):
  e,c=self.term['uv_err'],self.term['uv_cal']
  return (np.asarray(raw)*(1+e['gain'])+np.array(e['offset'])-np.array(c['offset']))/(1+c['gain'])
 def _uv_reading(self,raw):
  return np.clip(self._uv_unclipped(raw)-np.array(self.term['uv_zero']),0,6.4)
 def _allowed_tubes(self,ch):
  """Vessels the run may use: the assigned racks (A, B, C = 48 vessels each) in alphabetical order, starting at the first empty vessel, row or
  rack of the Start Collection setting (manual p.11-12, p.27); only empty vessels are used."""
  if not self.rack:return []
  n=self.rack['positions'];used=self.term['vessel_used'];out=[]                 # the software's record of used vessels (Empty / RFID clear it)
  mode=self.term['settings']['start_collection'];unit={'First Empty Vessel':1,'First Empty Row':ROW_SIZE,'First Empty Rack':RACK_SIZE}[mode]
  for r in sorted(self.term['setup'][ch]['racks']):
   lo=RACK_SIZE*'ABC'.index(r);hi=min(n,lo+RACK_SIZE)
   for b in range(lo,hi,unit):
    blk=range(b,min(b+unit,hi))
    if not any(used[j] for j in blk):out.extend(blk)
  return out
 def _rack_type(self,ch):
  """Rack type on the collection bed: from the RFID tag (manual p.1, p.11) or, with the reader off, the type selected in the Rack panel."""
  if self.term['settings']['rfid']:return RFID_TYPE if self.rack else None
  return self.term['setup'][ch].get('rack_type')
 def _rack_vessel_ml(self,ch):
  """Vessel volume the software fills to: the identified rack (RFID), else the selected type (the software cannot see a wrong selection)."""
  v=self.term['racks'][self._rack_type(ch) or RFID_TYPE]['vessel_volume_ml']
  return min(v,self.rack['vessel_volume_m3']/ML) if self.rack and self.term['settings']['rfid'] else v
 def _rack_range(self,rack):
  lo=RACK_SIZE*'ABC'.index(rack);hi=min(self.rack['positions'],lo+RACK_SIZE)
  if lo>=hi:raise ActionRejected('UNKNOWN_RACK')
  return lo,hi
 def _clear_rack(self,lo,hi):
  for j in range(lo,hi):self.term['vessel_used'][j]=False
  if self.term.get('allowed') is not None and self.rack:                    # a paused run may collect into the emptied vessels again
   al=[j for j in self._allowed_tubes(self.selected) if j>=self.tube or self.tube>=self.rack['positions']]
   self.term['allowed']=sorted(set(al)|({self.tube} if self.tube<self.rack['positions'] else set()))
 def _t_rack_reinsert(self,rack,vessels_emptied):
  """A rack taken off the collection bed and put back (manual p.15-16, Tables 1 and 3).  With the RFID reader on, a rack used only by previous runs
  is cleared in the software at once; a rack used by the run in progress, and every rack with the reader off, stays marked used until Empty."""
  if not self.rack:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and not self.term['paused']:raise ActionRejected('BUSY')   # racks are added / removed with the run paused (manual p.15)
  lo,hi=self._rack_range(rack)
  if vessels_emptied:                                                       # the fractions leave the instrument with their vessels (still collected)
   self.removedfluid+=self.fractionfluid[lo:hi].sum(axis=0);self.removedmol+=self.fractionmol[lo:hi].sum(axis=0)
   self.fractionfluid[lo:hi]=0.;self.fractionmol[lo:hi]=0.
  in_progress=self.phase=='running' and bool(self.term['runs']) and any(lo<=j<hi for j in self.term['runs'][-1]['tubes'])
  cleared=bool(self.powered and not self.term['shutdown'] and self.term['settings']['rfid'] and not in_progress)
  if cleared:self._clear_rack(lo,hi)
  return dict(rack=rack,cleared=cleared)
 def _t_rack_empty(self,rack):
  self._need_on()
  if not self.rack:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and not self.term['paused']:raise ActionRejected('BUSY')
  lo,hi=self._rack_range(rack);self._clear_rack(lo,hi);return dict(rack=rack)
 def _jump(self):
  """Keep the collection pointer on the run's allowed vessels (skips disabled racks and used vessels); none left: the run pauses and asks for
  more racks (manual p.15)."""
  al=self.term.get('allowed')
  if al is None or self.tube in al or not self.rack:return
  nxt=[j for j in al if j>self.tube];self.tube=nxt[0] if nxt else self.rack['positions']
  if not nxt and self.phase=='running' and self.stage=='gradient':self._collection_full()
 def _collection_full(self):
  if self.term.get('allowed') is not None:                                  # terminal run: pause, arm home, assign more racks
   self.term['paused']=True;self.term['pause_reason']='Load or assign more racks';self.x,self.y=0.,.33;self.fraction=0;self.flow=0.
  else:self._fail('COLLECTION_FULL')
 def _valley(self):
  """Valley Fractionation (manual p.27): a minimum between two peaks while collecting moves to the next vessel.  The detection rule is not
  published: assumption - the signal fell and then rose again by 5 % of the minimum (at least 2 mAU)."""
  v=self.term['valley'];m=self.methods[self.selected]
  if not (self.term['settings']['valley_fractionation'] and self.phase=='running' and self.stage=='gradient' and self.fraction>0 and m):v.update(prev=None,low=None);return
  mask=np.array(m.get('collect_mask',[True,True]));x=float(np.max(self.uv[mask])) if mask.any() else 0.
  if v['prev'] is not None and x<v['prev']-1e-9:v['low']=x if v['low'] is None else min(v['low'],x)
  elif v['low'] is not None and x>v['low']+max(.002,.05*v['low']):
   self.tube+=1;self._jump();v['low']=None
  v['prev']=x
 def _t_run_setup(self,channel,settings):
  self._need_on()
  s=copy.deepcopy(self.term['setup'][channel]);running=self.phase=='running' and self.selected==channel and self.stage!='standby'
  live={'comments','flow_ml_min','uv1_use','uv2_use','uv1_nm','uv2_nm','threshold_mau','collect_all','segments','unit'}|({'racks','max_fraction_ml'} if self.term['paused'] else set())
  if running and set(settings)-live:raise ActionRejected('BUSY')            # edit on the fly; paused: also racks and Max Fraction Volume (manual p.16)
  if 'column_type' in settings and settings['column_type'] is not None:
   c=self._column_type(settings['column_type'])
   if not c['visible']:raise ActionRejected('UNKNOWN_COLUMN')
   if s['column_type']!=settings['column_type']:s['column_id']=None;s['flow_ml_min']=int(c['default_flow_ml_min'])   # default flow preselected (manual p.10)
  for k in ('solvent_a','solvent_b','modifier'):
   if settings.get(k) is not None and (settings[k] not in self.term['solvents'] or not self.term['solvents'][settings[k]]['visible']):raise ActionRejected('UNKNOWN_SOLVENT')
  if settings.get('rack_type') is not None and settings['rack_type'] not in self.term['racks']:raise ActionRejected('UNKNOWN_RACK')
  if settings.get('user') is not None and settings['user'] not in self.term['users']:raise ActionRejected('UNKNOWN_USER')
  settings=copy.deepcopy(settings)
  for k in ('sample_mass_g','uv1_nm','uv2_nm','threshold_mau','max_fraction_ml','modifier_pct'):
   if settings.get(k) is not None:settings[k]=float(settings[k])
  if 'segments' in settings:settings['segments']=[{k:float(v) for k,v in g.items()} for g in settings['segments']]
  if settings.get('flow_ml_min') is not None:
   if abs(settings['flow_ml_min']-round(settings['flow_ml_min']))>1e-9:raise ActionRejected('INVALID_ARGUMENT')   # 1 mL/min increments (datasheet)
   settings['flow_ml_min']=int(round(settings['flow_ml_min']))
  s.update(settings)
  self.term['setup'][channel]=s
  if running and self.methods[channel]:                                   # edit on the fly (manual p.16): flow, collection and gradient apply at once
   m=self.methods[channel]
   if s['flow_ml_min'] is not None:m['flow_m3_s']=s['flow_ml_min']*ML/60.
   m['collection_mode']='all' if s['collect_all'] else 'threshold';m['threshold_absorbance']=s['threshold_mau']/1000.
   m['collect_mask']=[s['uv1_use']=='Collect',s['uv2_use']=='Collect']
   if 'max_fraction_ml' in settings:
    vessel=self._rack_vessel_ml(channel);m['fraction_volume_m3']=min(vessel,s['max_fraction_ml'] or vessel)*ML
   if 'racks' in settings and self.term.get('allowed') is not None:
    al=[j for j in self._allowed_tubes(channel) if j>=self.tube or self.tube>=self.rack['positions']];self.term['allowed']=sorted(set(al)|({self.tube} if self.tube<self.rack['positions'] else set()))
   if 'segments' in settings or 'flow_ml_min' in settings:
    nodes=self._gradient_times(channel);m['gradient']=nodes;m['timeout_s']=max(m['timeout_s'],2*nodes[-1]['time_s']+600.)
  return dict(setup=copy.deepcopy(s))
 def _t_column_scan(self,channel,column_type,column_id):
  self._need_on();c=self._column_type(column_type)
  if self.phase=='running' and self.selected==channel:raise ActionRejected('BUSY')
  s=self.term['setup'][channel];s.update(column_type=column_type,column_id=column_id,flow_ml_min=int(c['default_flow_ml_min']));return dict(setup=copy.deepcopy(s))
 def _t_run_prepare(self,channel):
  self._need_on();self._need_idle()
  issues=self._setup_issues(channel)
  if issues:raise ActionRejected('SETUP_INCOMPLETE')
  if self.done[channel] or (self.selected!=channel and not self._can_service()):raise ActionRejected('PRECONDITION_FAILED')
  if self.selected!=channel:self.selected=channel;self.phase='idle' if self.gradient_s[channel]==0 else 'cancelled';self.stage='standby';self.elapsed_s=0.;self.fault=''
  s=self.term['setup'][channel]
  conv=self._conversion(channel) if self.gradient_s[channel]==0 else []
  if s['equilibration'] and self.gradient_s[channel]==0:                     # column equilibration with the initial mix, then Sample Load (manual p.14, p.25)
   if not self.plant or not self.columns[channel]:raise ActionRejected('PRECONDITION_FAILED')
   c=self.term['columns'][s['column_type']];comp=[0.]*4;a,b=self._inlet_of(s['solvent_a']),self._inlet_of(s['solvent_b']);x=s['segments'][0]['start_pct']/100
   mod=self._set_modifier(channel);fm=mod['fraction'] if mod else 0.
   comp[a]+=(1-x)*(1-fm);comp[b]+=x*(1-fm)
   if mod:comp[mod['inlet']]+=fm
   eq=dict(kind='liquid',path=channel,comp=comp,q=c['equil_flow_ml_min']*ML/60.,left=c['equil_length_cv']*c['cv_ml']*ML,dest='waste',
           p_cap=c['equil_pressure_bar']*1e5,after='sample_load')
   if conv:self._begin_flush(dict(conv[0],then=[(f,'conversion') for f in conv[1:]]+[(eq,'equilibration')]),'conversion')
   else:self._begin_flush(eq,'equilibration')
   return dict(channel=channel,issues=[])
  if conv:                                                                   # conversion, then Sample Load
   conv[-1]['after']='sample_load_now';self._begin_flush(dict(conv[0],then=[(f,'conversion') for f in conv[1:]]),'conversion')
   return dict(channel=channel,issues=[])
  self.term['pending']=channel;self.stage='sample_load' if self.phase!='running' else self.stage;return dict(channel=channel,issues=[])
 def _t_run_unprepare(self):
  ch=self.term['pending'];self.term['pending']=None
  if self.stage=='sample_load':self.stage='standby'
  return dict(channel=ch)
 def _t_run_begin(self):
  self._need_on();ch=self.term['pending']
  if ch is None:raise ActionRejected('NOT_WAITING')
  s=self.term['setup'][ch];nodes=self._gradient_times(ch);col=self.term['columns'][s['column_type']]
  vessel=self._rack_vessel_ml(ch)
  frac=min(vessel,s['max_fraction_ml'] or vessel)                           # Max Fraction Volume (Rack panel) up to the vessel volume
  asp=min(self.term['solvents'][n]['max_aspiration_ml_min'] for n in ([s['solvent_a'],s['solvent_b']]+([s['modifier']] if self._modifier(ch) else [])))
  flow=max(1,min(s['flow_ml_min'],int(asp*300/420)))                       # aspiration-limited flow (420 mL/min aspiration for 300 mL/min, manual p.26)
  method=dict(channel=ch,flow_m3_s=flow*ML/60.,gradient=nodes,wavelengths_m=[s['uv1_nm']*1e-9,s['uv2_nm']*1e-9],
              collection_mode='all' if s['collect_all'] else 'threshold',threshold_absorbance=min(6.4,s['threshold_mau']/1000.),fraction_volume_m3=frac*ML,
              timeout_s=min(21600.,2*nodes[-1]['time_s']+600.))
  if not self.samples[ch]:raise ActionRejected('NO_SAMPLE')                # load the sample before starting the gradient (manual p.13)
  if self.methods[ch] is None or self.gradient_s[ch]==0:
   self.methods[ch]=None
   self.invoke('method.configure',**method)
  self.methods[ch]['collect_mask']=[s['uv1_use']=='Collect',s['uv2_use']=='Collect']
  self.methods[ch]['modifier']=self._set_modifier(ch)
  self.methods[ch]['pressure_cap_pa']=col['max_pressure_bar']*1e5          # Max Pressure: the flow is reduced to hold it (manual p.25)
  allowed=self._allowed_tubes(ch)
  out=self.invoke('acquisition.start')
  self.term['allowed']=allowed;self.tube=allowed[0]                         # Start Collection / rack assignment (manual p.11-12)
  self.term['uv_zero']=[0.,0.];self.term['uv_zero']=self._uv_unclipped(self._uv_raw()).tolist()   # automatic UV Zero on the initial mix (manual p.27)
  self.term['pending']=None;return out
 def _t_run_pause(self):
  self._need_on()
  if self.phase!='running' or self.stage not in ('gradient','equilibration'):raise ActionRejected('NOT_RUNNING')
  if self.term['paused']:raise ActionRejected('PAUSED')
  self.term['paused']=True;self.term['pause_reason']='Paused by the user';self.x,self.y=0.,.33;return dict(paused=True)
 def _t_run_resume(self):
  self._need_on()
  if not self.term['paused']:raise ActionRejected('NOT_PAUSED')
  if self.term.get('allowed') is not None and self.rack and self.tube>=self.rack['positions']:
   free=self._allowed_tubes(self.selected)
   if not free:raise ActionRejected('COLLECTION_FULL')                      # still no empty vessel in the assigned racks
   self.term['allowed']=free;self.tube=free[0]
  self.term['paused']=False;self.term['pause_reason']=None;return dict(paused=False)
 def _t_run_end(self,option):
  self._need_on()
  if self.phase!='running' or self.stage not in ('gradient','gradient_completed','equilibration','conversion'):raise ActionRejected('NOT_RUNNING')
  if self.stage=='conversion' and option=='skip_equilibration':raise ActionRejected('NOT_EQUILIBRATING')
  if self.stage in ('equilibration','conversion'):                           # End during the equilibration (System Paused dialog, manual p.15) or conversion
   ph=self.term['flush']['restore'][0];self.term.update(flush=None,paused=False,pause_reason=None);self.flow=0.
   if option=='abort':self.phase=ph if ph!='running' else 'idle';self.stage='standby'
   else:self.stage='purging';self.term['after_purge']=('sample_load' if option=='skip_equilibration' else 'idle',ph)
   return dict(phase=self.phase,stage=self.stage)
  if option=='skip_equilibration':raise ActionRejected('NOT_EQUILIBRATING')
  self.term.update(paused=False,pause_reason=None,hold=False,awaiting=None)
  if option=='abort':self.phase='cancelled';self.flow=0.;self.fraction=0;self.stage='aborted'
  else:self._start_cleanup(self.selected)
  return dict(phase=self.phase,stage=self.stage)
 def _t_run_hold(self,on):
  self._need_on()
  if self.phase!='running' or self.stage!='gradient':raise ActionRejected('NOT_RUNNING')
  self.term['hold']=on;return dict(hold=on)
 def _t_run_next_vessel(self):
  self._need_on()
  if self.phase!='running' or self.stage!='gradient' or self.fraction==0:raise ActionRejected('NOT_COLLECTING')
  t0=self.tube;self.tube+=1;self._jump()
  if self.tube>=self.rack['positions']:self.tube=t0;raise ActionRejected('COLLECTION_FULL')
  return dict(fraction_number=self.fraction)
 def _t_run_uv_zero(self):
  self._need_on()
  if not self.term['settings']['manual_uv_zero']:raise ActionRejected('SETTING_DISABLED')
  if self.phase!='running' or self.stage!='gradient':raise ActionRejected('NOT_RUNNING')
  self.term['uv_zero']=self._uv_unclipped(np.array(self.term['uv_raw'])).tolist();self.uv=np.zeros(2);return dict(uv_zero_au=self.term['uv_zero'])
 def _extend(self,i):
  m=self.methods[i];g=m['gradient'];t=g[-1]['time_s'];add=EXTEND_FRACTION*t
  g.append(dict(time_s=t+add,strong_fraction=g[-1]['strong_fraction']));m['timeout_s']=min(21600.,m['timeout_s']+add);self.term['extended']=True
  return t+add
 def _t_run_extend(self):
  self._need_on();i=self.selected
  if self.phase!='running' or self.stage not in ('gradient','gradient_completed'):raise ActionRejected('NOT_RUNNING')
  end=self._extend(i);self.stage='gradient';self.term['awaiting']=None;return dict(gradient_end_s=end)
 def _gradient_end(self,i):
  """End of the gradient stage (manual p.16-17): Auto Extend while a collection signal is above the threshold, the Gradient Run Completed dialog
  with Confirm Cleanup, else the cleanup."""
  m=self.methods[i];s=self.term['settings'];mask=np.array(m.get('collect_mask',[True,True]))
  above=bool(mask.any() and float(np.max(self.uv[mask]))>=m['threshold_absorbance'])
  if s['auto_extend'] and not self.term['extended'] and above:self._extend(i);return
  self.flow=0.;self.fraction=0
  if s['confirm_cleanup']:self.stage='gradient_completed';self.term['awaiting']='gradient_completed';return
  self._start_cleanup(i)
 def _start_cleanup(self,i):
  s=self.term['settings'];q=[]
  if s['line_flush']!='Off':q.append(('line',s['line_flush']))
  q.append(('purge',None))
  if s['detector_flush']!='Off':q.append(('detector',s['detector_flush']))
  self.term['cleanup']=q;self.term['awaiting']=None;self.term['hold']=False;self._next_cleanup(i)
 def _next_cleanup(self,i):
  q=self.term['cleanup']
  if not q:return False
  kind,dest=q.pop(0)
  if kind=='purge':self.stage='purging';return True
  pair=self.columns[i]['solvent_pair'] if self.columns[i] else [0,1];comp=[0.]*4;comp[pair[0] if kind=='line' else pair[1]]=1.
  vol=LINE_FLUSH_ML if kind=='line' else DETECTOR_FLUSH_ML
  self.term['flush']=dict(kind='liquid',path=i if kind=='line' else None,comp=comp,q=CLEANUP_ML_MIN*ML/60.,left=vol*ML,dest='collect' if dest=='Collect' else 'waste',chain=True,restore=None)
  self.stage='line_flush' if kind=='line' else 'detector_flush';return True
 def _begin_flush(self,f,stage):
  if not self.powered or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  self._need_idle()
  if self.tray>0:raise ActionRejected('PRECONDITION_FAILED')
  for k,c in enumerate(f['comp']):
   if c>0 and self.reservoir[k]<c*f['left']-1e-15:raise ActionRejected('SOLVENT_EMPTY')
  self._freeze_runs();f.update(chain=False,restore=(self.phase,self.stage));self.term['flush']=f;self.phase='running';self.stage=stage;self.fault=''
  self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);return dict(phase=self.phase,operation_id=self.operation_id)
 def _t_purge_start(self,channel):
  self._need_on();self._need_idle()
  if self.pressure[channel]<=1.:raise ActionRejected('NO_PRESSURE')
  if channel!=self.selected:self.selected=channel;self.phase='idle' if self.gradient_s[channel]==0 else 'cancelled';self.stage='standby';self.elapsed_s=0.;self.fault=''
  return self.invoke('pressure.release')
 def _t_solvents_setup(self,inlet,solvent,capacity_ml=None,level_ml=None):
  self._need_on()
  if solvent is not None and (solvent not in self.term['solvents'] or not self.term['solvents'][solvent]['visible']):raise ActionRejected('UNKNOWN_SOLVENT')
  if capacity_ml is not None and level_ml is not None and level_ml>capacity_ml:raise ActionRejected('LEVEL_ABOVE_CAPACITY')
  x=self.term['inlets'][inlet];x['solvent']=solvent
  if capacity_ml is not None:x['capacity_ml']=capacity_ml
  if level_ml is not None:x['level_ml']=level_ml;x['ref_m3']=float(self.reservoir[inlet])
  return dict(inlet=inlet)
 def _t_waste_setup(self,capacity_ml=None,level_ml=None):
  self._need_on();w=self.term['waste'];cap=capacity_ml if capacity_ml is not None else w['capacity_ml']
  if cap is not None and level_ml is not None and level_ml>cap:raise ActionRejected('LEVEL_ABOVE_CAPACITY')
  if capacity_ml is not None:w['capacity_ml']=capacity_ml
  if level_ml is not None:w.update(level_ml=level_ml,ref_m3=float(self.wastefluid.sum()))
  return dict(capacity_ml=w['capacity_ml'])
 def _t_solvents_prime(self,inlet):
  self._need_on()
  if self.term['inlets'][inlet]['solvent'] is None:raise ActionRejected('NO_SOLVENT')
  comp=[0.]*4;comp[inlet]=1.
  return self._begin_flush(dict(kind='liquid',path=None,comp=comp,q=PRIME_ML_MIN*ML/60.,left=PRIME_ML*ML,dest='waste'),'prime')
 def _t_flush_start(self,path,column_type,volume_ml,flow_ml_min,percents):
  self._need_on()
  if abs(sum(percents)-100.)>1e-6:raise ActionRejected('PERCENT_SUM')
  if abs(flow_ml_min-round(flow_ml_min))>1e-9:raise ActionRejected('INVALID_ARGUMENT')
  ch=None if path=='bypass' else int(path[-1])-1
  if column_type is not None:
   c=self._column_type(column_type);flow_ml_min=min(flow_ml_min,int(c['default_flow_ml_min']*3))
  return self._begin_flush(dict(kind='liquid',path=ch,comp=[p/100. for p in percents],q=flow_ml_min*ML/60.,left=volume_ml*ML,dest='waste'),'flush')
 def _t_flush_air(self,channel,column_type,volume_ml=None):
  self._need_on()
  if volume_ml is None:
   if column_type is None:raise ActionRejected('VOLUME_REQUIRED')
   c=self._column_type(column_type);volume_ml=c['air_flush_length_cv']*c['cv_ml']
  q=(self._column_type(column_type)['default_flow_ml_min'] if column_type else 50.)*ML/60.
  return self._begin_flush(dict(kind='air',path=channel,comp=[0.]*4,q=q,left=volume_ml*ML,dest='waste'),'air_flush')
 def _flush_advance(self,dt):
  """One step of a flush (prime, Flush, Air Flush, line or detector flush; manual p.7, p.23-24, p.27-28): solvent from the inlets (or air)
  through the mixer, the column of the channel (if any) and the detector to waste or the current vessel; the column outflow follows the same cell
  transport as the gradient (retained solute partly eluted), air pushes the column liquid out."""
  f=self.term['flush'];q=f['q'];vm=self.plant['mixer_volume_m3'];vd=self.plant['detector_volume_m3'];i=f['path']
  c=self.columns[i] if i is not None else None;cv=c['void_volume_m3']/N if c else None
  h=min(dt,f['left']/q,.2*min(vm,vd,cv or vm)/q)
  supply=np.array(f['comp'])*q*h
  if np.any(self.reservoir<supply-1e-18):self._fail('SOLVENT_EMPTY');return
  m=self.methods[i if i is not None else self.selected]
  if f['dest']=='collect' and m:                                                  # collected flushes use the run's collection criteria (manual p.27)
   mask=np.array(m.get('collect_mask',[True,True]));want=m['collection_mode']=='all' or bool(mask.any() and float(np.max(self.uv[mask]))>=m['threshold_absorbance'])
  else:want=False
  if want and self.rack is not None and self.tube<self.rack['positions'] and self.fractionfluid[self.tube].sum()+q*h>min(m['fraction_volume_m3'],self.rack['vessel_volume_m3'])+1e-15:
   self.tube+=1;self._jump()
  dest_tube=want and self.rack is not None and self.tube<self.rack['positions']
  if not dest_tube and self.wastefluid.sum()+q*h>self.plant['waste_capacity_m3']+1e-15:self._fail('WASTE_FULL');return
  if f['kind']=='air':
   liq=self.colfluid[i].sum();frac=min(1.,q*h/liq) if liq>1e-18 else 0.
   k=self.k0[i,:,None]*np.exp(-self.slope[i,:,None]*self._strong(i,cv)[None,:])
   out=self.colfluid[i]*frac;sol=self.colmol[i]*frac/(1+k);self.colfluid[i]-=out;self.colmol[i]-=sol
   self.wastefluid+=out.sum(axis=1);self.wastemol+=sol.sum(axis=1);self.pressure[i]=.1*q*c['resistance_pa_s_m3']
  else:
   if c is not None and f.get('p_cap') and q*c['resistance_pa_s_m3']>f['p_cap']:   # Equilibration Pressure: the flow is reduced to hold it (manual p.25)
    q=f['p_cap']/c['resistance_pa_s_m3'];supply=np.array(f['comp'])*q*h
   mixout=self.mixer/vm*q*h;self.reservoir-=supply;self.mixer+=supply-mixout
   if c is not None:
    strong=self._strong(i,cv);k=self.k0[i,:,None]*np.exp(-self.slope[i,:,None]*strong[None,:])
    colout=self.colfluid[i]/cv*q*h;solout=self.colmol[i]/(cv*(1+k))*q*h
    self.colfluid[i]-=colout;self.colfluid[i,:,0]+=mixout;self.colfluid[i,:,1:]+=colout[:,:-1];detin,detsol=colout[:,-1],solout[:,-1]
    self.colmol[i]-=solout;self.colmol[i,:,1:]+=solout[:,:-1]
    self.pressure[i]=q*c['resistance_pa_s_m3']
    if self.pressure[i]>min(3e6,c['pressure_limit_pa']):self._fail('OVERPRESSURE');return
   else:detin,detsol=mixout,np.zeros(self.ns)
   detout=self.detfluid/vd*q*h;detmolout=self.detmol/vd*q*h;self.detfluid+=detin-detout;self.detmol+=detsol-detmolout
   if dest_tube:self.fractionfluid[self.tube]+=detout;self.fractionmol[self.tube]+=detmolout;self.fraction=self.tube+1;self.term['vessel_used'][self.tube]=True
   else:self.wastefluid+=detout;self.wastemol+=detmolout
   if self.plant:raw=self._uv_raw();self.term['uv_raw']=[float(x) for x in raw];self.uv=self._uv_reading(raw)
  self.flow=q;f['left']-=q*h;self.elapsed_s+=h
  if f['left']<=1e-15:
   self.flow=0.;self.term['flush']=None
   if f.get('then'):                                                         # next step of a chained preparation (conversion, equilibration)
    nf,st_=f['then'][0];self.term['flush']=dict(nf,then=f['then'][1:],chain=False,restore=f['restore']);self.stage=st_;return h
   if f['chain']:
    if not self._next_cleanup(self.selected):self._finish_run(self.selected)
   elif f.get('after')=='sample_load':self.term['after_purge']=('sample_load',f['restore'][0]);self.stage='purging'
   elif f.get('after')=='sample_load_now':
    ph=f['restore'][0];self.phase=ph if ph!='running' else 'idle';self.term['pending']=self.selected;self.stage='sample_load'
   else:self.phase,self.stage=f['restore'][0] if f['restore'][0]!='running' else 'idle','flushed'
  return h
 def _finish_run(self,i):
  m=self.methods[i];self.phase='succeeded';self.stage='finished_residual_solute';self.done[i]=bool(m and self.gradient_s[i]>=m['gradient'][-1]['time_s']-1e-8)
 def _record_run(self):
  t=self.term;i=self.selected;s=t['setup'][i];t['run_seq']+=1;now=self._clock()
  t['runs'].append(dict(run_id=f'run_{t["run_seq"]}',name=s['run_name'] or now.replace('T',' '),user=s['user'],channel=i+1,column_type=s['column_type'],
                        column_id=s['column_id'],chemistry=t['columns'].get(s['column_type'],{}).get('chemistry','normal'),started=now,operation_id=self.operation_id,start_time_s=self.time_s,
                        history_from=len(self.history),favorite=False,note='',apply_report_setup=False,pictures=[],tubes=[],
                        report={k:True for k in REPORT_KEYS},
                        setup=copy.deepcopy(s),comments=s['comments']))
 def _run(self,run_id):
  r=next((x for x in self.term['runs'] if x['run_id']==run_id),None)
  if r is None:raise ActionRejected('UNKNOWN_RUN')
  return r
 def _t_results_update(self,run_id,favorite=None,note=None,apply_report_setup=None,report=None,add_picture=None,delete_picture=None):
  self._need_on();r=self._run(run_id)
  if add_picture is not None:                                               # To Report in the Analysis view: a picture of the shown chromatogram
   if len(r['pictures'])>=20:raise ActionRejected('TOO_MANY_PICTURES')
   r['pictures'].append(copy.deepcopy(add_picture))
  if delete_picture is not None:
   if not 0<=delete_picture<len(r['pictures']):raise ActionRejected('INVALID_ARGUMENT')
   del r['pictures'][delete_picture]
  if report is not None and (set(report)-set(r['report']) or any(type(v) is not bool for v in report.values())):raise ActionRejected('INVALID_ARGUMENT')
  for k,v in dict(favorite=favorite,note=note,apply_report_setup=apply_report_setup).items():
   if v is not None:r[k]=v
  if report:r['report'].update(report)
  return dict(run_id=run_id)
 def _t_results_export(self,run_id):
  self._need_on();r=self._run(run_id)
  if self._run_status(r) in ('running','paused'):raise ActionRejected('BUSY')
  files=[f'biotage/selekt/{r["user"] or "unknown"}/{r["name"]}.pdf',f'biotage/selekt/results/{r["run_id"]}.xml',f'biotage/selekt/results/{r["run_id"]}.spectrum']
  self.term['usb']=sorted(set(self.term['usb'])|set(files));return dict(files=files)
 def _t_results_create_run(self,run_id,channel):
  self._need_on();r=self._run(run_id)
  if self.phase=='running' and self.selected==channel:raise ActionRejected('BUSY')
  s=copy.deepcopy(r['setup']);s.update(run_name='',column_id=None);self.term['setup'][channel]=s;return dict(setup=copy.deepcopy(s))
 def _t_results_delete(self,run_ids):
  self._need_owner()
  for x in run_ids:
   r=self._run(x)
   if self._run_status(r) in ('running','paused'):raise ActionRejected('BUSY')
  self.term['runs']=[r for r in self.term['runs'] if r['run_id'] not in run_ids];return dict(deleted=len(set(run_ids)))
 def _run_status(self,r):
  if r['operation_id']==self.operation_id:
   return 'paused' if (self.phase=='running' and self.term['paused']) else {'running':'running','succeeded':'finished','cancelled':'finished','failed':'failed'}.get(self.phase,'finished')
  return r.get('final','finished')
 def _t_owner_login(self,user,password):
  self._need_on();u=self.term['users'].get(user)
  if u is None or u['privilege']!='System Owner':raise ActionRejected('OWNER_REQUIRED')
  if (u['password'] or '')!=password:raise ActionRejected('WRONG_PASSWORD')
  self.term['owner']=True;return dict(unlocked=True)
 def _t_owner_logout(self):
  self.term['owner']=False;return dict(unlocked=False)
 def _admin_entry(self,lib,entry,fields,key='name',original=None):
  """Add or edit an entry of a library; original = its name before the edit (a rename).  Biotage entries: only Visible changes (manual p.25)."""
  self._need_owner()
  name=entry.get(key)
  if not isinstance(name,str) or not 1<=len(name.strip())<=40:raise ActionRejected('INVALID_ARGUMENT')
  if original is not None and original!=name:
   if original not in self.term[lib]:raise ActionRejected('INVALID_ARGUMENT')
   if self.term[lib][original].get('builtin'):raise ActionRejected('READ_ONLY')
   if name in self.term[lib]:raise ActionRejected('NAME_EXISTS')
   self.term[lib][name]=self.term[lib].pop(original)
  old=self.term[lib].get(name)
  if old is not None and old.get('builtin') and any(entry[k]!=old.get(k) for k in entry if k not in (key,'visible')):raise ActionRejected('READ_ONLY')
  new=dict(old) if old else dict(fields)
  for k,v in entry.items():
   if k not in fields:raise ActionRejected('INVALID_ARGUMENT')
   if type(v) is not type(fields[k]) and not (isinstance(fields[k],float) and isinstance(v,int) and type(v) is not bool) and fields[k] is not None:raise ActionRejected('INVALID_ARGUMENT')
   new[k]=float(v) if isinstance(fields[k],float) else v
  new[key]=name;self.term[lib][name]=new;return name
 def _t_columns_save(self,entry,original=None):
  base=dict(name='',visible=True,builtin=False,cv_ml=10.,default_flow_ml_min=10.,max_pressure_bar=10.,equil_pressure_bar=5.,equil_length_cv=3.,equil_flow_ml_min=10.,
            air_flush_length_cv=3.,load_capacity_g=.1,chemistry='normal',part_number='')
  if 'builtin' in entry or (entry.get('chemistry') not in (None,'normal','reversed')):raise ActionRejected('INVALID_ARGUMENT')
  for k in ('cv_ml','default_flow_ml_min','max_pressure_bar','equil_flow_ml_min'):
   if k in entry and not (isinstance(entry[k],(int,float)) and not isinstance(entry[k],bool) and 0<entry[k]<=(3100 if k=='cv_ml' else 300 if 'flow' in k else 30)):raise ActionRejected('INVALID_ARGUMENT')
  return dict(name=self._admin_entry('columns',entry,base,original=original))
 def _t_columns_delete(self,name):
  self._need_owner();c=self._column_type(name)
  if c['builtin']:raise ActionRejected('READ_ONLY')
  if any(s['column_type']==name for s in self.term['setup']):raise ActionRejected('IN_USE')
  del self.term['columns'][name];return dict(name=name)
 def _t_solvents_save(self,entry,original=None):
  base=dict(name='',visible=True,builtin=False,strength=.5,max_aspiration_ml_min=420.)
  if 'builtin' in entry or ('strength' in entry and not (isinstance(entry['strength'],(int,float)) and 0<=entry['strength']<=1.1)):raise ActionRejected('INVALID_ARGUMENT')
  if original is not None and original!=entry.get('name') and any(x['solvent']==original for x in self.term['inlets']):raise ActionRejected('IN_USE')
  return dict(name=self._admin_entry('solvents',entry,base,original=original))
 def _t_solvents_delete(self,name):
  self._need_owner();s=self.term['solvents'].get(name)
  if s is None:raise ActionRejected('UNKNOWN_SOLVENT')
  if s['builtin']:raise ActionRejected('READ_ONLY')
  if any(x['solvent']==name for x in self.term['inlets']):raise ActionRejected('IN_USE')
  del self.term['solvents'][name];return dict(name=name)
 def _t_racks_set_volume(self,name,vessel_volume_ml):
  self._need_owner();r=self.term['racks'].get(name)
  if r is None:raise ActionRejected('UNKNOWN_RACK')
  if vessel_volume_ml is not None and vessel_volume_ml>r['default_ml']:raise ActionRejected('VOLUME_ABOVE_DEFAULT')    # only lowered (manual p.26)
  r['vessel_volume_ml']=r['default_ml'] if vessel_volume_ml is None else float(vessel_volume_ml);return dict(name=name,vessel_volume_ml=r['vessel_volume_ml'])
 def _t_users_save(self,entry,original=None):
  base=dict(name='',visible=True,password=None,privilege='Chemist',email=None,auto_send_report=False,email_notifications=False)
  if entry.get('privilege') not in (None,'Chemist','System Owner'):raise ActionRejected('INVALID_ARGUMENT')
  for k in ('password','email'):
   if entry.get(k) is not None and not isinstance(entry[k],str):raise ActionRejected('INVALID_ARGUMENT')
  old=self.term['users'].get(original if original is not None else entry.get('name'),{})
  if old.get('privilege')=='System Owner' and entry.get('privilege')=='Chemist' and sum(u['privilege']=='System Owner' for u in self.term['users'].values())<=1:raise ActionRejected('LAST_OWNER')
  fields=dict(base);fields['password']='';fields['email']=''
  e={k:(v if v is not None else '') if k in ('password','email') else v for k,v in entry.items()}
  if old and e.get('password')=='':e.pop('password')                         # editing: an empty password field keeps the password
  name=self._admin_entry('users',e,fields,original=original)
  if original is not None and original!=name:                               # renaming updates the reports and run setups (manual p.26)
   for r in self.term['runs']:
    if r['user']==original:r['user']=name
   for s_ in self.term['setup']:
    if s_['user']==original:s_['user']=name
  u=self.term['users'][name];u['password']=u['password'] or None;u['email']=u['email'] or None;return dict(name=name)
 def _t_users_delete(self,name):
  self._need_owner()
  if name not in self.term['users']:raise ActionRejected('UNKNOWN_USER')
  if self.term['users'][name]['privilege']=='System Owner' and sum(u['privilege']=='System Owner' for u in self.term['users'].values())<=1:raise ActionRejected('LAST_OWNER')
  del self.term['users'][name]
  for r in self.term['runs']:
   if r['user']==name:r['user']=None                                     # reports stay, without the user name (manual p.26)
  for s in self.term['setup']:
   if s['user']==name:s['user']=None
  return dict(name=name)
 def _t_settings_set(self,changes):
  self._need_owner();s=dict(self.term['settings']);restart=False
  for k,v in changes.items():
   if k not in SETTINGS_DEFAULT:raise ActionRejected('INVALID_ARGUMENT')
   if k in SETTING_CHOICES and v not in SETTING_CHOICES[k]:raise ActionRejected('INVALID_ARGUMENT')
   if k in ('date','time'):
    import re
    if not isinstance(v,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}' if k=='date' else r'\d{2}:\d{2}:\d{2}',v):raise ActionRejected('INVALID_ARGUMENT')
    self.term['pending_settings'][k]=v;restart=True;continue           # date and time take effect after a restart (manual p.27)
   if k in ('external_tube_volume_ml','external_max_flow_ml_min'):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=1000:raise ActionRejected('INVALID_ARGUMENT')
   elif k not in SETTING_CHOICES and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
   s[k]=v
  self.term['settings']=s;return dict(settings=copy.deepcopy(s),restart_required=restart)
 def _t_database_backup(self):
  self._need_owner();self._need_idle()
  name=f'selekt_backup_{len([f for f in self.term["usb"] if f.startswith("biotage/selekt/backup/")])+1}.bak'
  self.term.setdefault('backups',{})[name]=copy.deepcopy({k:self.term[k] for k in ('runs','columns','solvents','racks','users','run_seq')})
  self.term['usb']=sorted(set(self.term['usb'])|{'biotage/selekt/backup/'+name});self.term['restart_required']=True;return dict(file=name)
 def _t_database_restore(self,file):
  self._need_owner();self._need_idle()
  b=self.term.get('backups',{}).get(file)
  if b is None or 'biotage/selekt/backup/'+file not in self.term['usb']:raise ActionRejected('NO_BACKUP')
  self.term.update(copy.deepcopy(b));self.term['restart_required']=True;return dict(file=file)
 def _t_logs_export(self):
  self._need_owner();self.term['logs']+=1;name=f'biotage/selekt/logs/selekt_logs_{self.term["logs"]}.zip'
  self.term['usb']=sorted(set(self.term['usb'])|{name});return dict(file=name)
 def _t_uv_calibrate(self):
  """Calibrate UV Detector (manual p.30): with the flow cell filled with clean solvent the zero is read, the internal reference gives the gain;
  the correction is stored and applied to UV1 / UV2 (solute left in the cell enters the zero, as on the device)."""
  self._need_owner();self._need_idle()
  e=self.term['uv_err'];raw=np.array(self.term['uv_raw'])
  self.term['uv_cal']=dict(offset=(np.array(e['offset'])+raw*(1+e['gain'])).tolist(),gain=float(e['gain']));self.term['uv_zero']=[0.,0.]
  return dict(offset_au=self.term['uv_cal']['offset'],gain=self.term['uv_cal']['gain'])
 def _t_sensor_configure_error(self,uv_offset_au,uv_gain):
  self.term['uv_err']=dict(offset=list(uv_offset_au),gain=float(uv_gain))
  if self.plant and self.phase!='running':self.uv=self._uv_reading(self._uv_raw())
  return dict(configured=True)
 def _t_system_shutdown(self):
  self._need_on()
  if self.phase=='running':raise ActionRejected('BUSY')
  self.term.update(shutdown=True,owner=False,pending=None);return dict(shutdown=True)
 def _power_up(self):
  t=self.term
  import datetime
  if t['pending_settings']:                                                # date / time set in System Settings apply at the restart
   now=datetime.datetime.fromisoformat(self._clock());d=t['pending_settings'].get('date',now.date().isoformat());tm=t['pending_settings'].get('time',now.time().isoformat())
   want=datetime.datetime.fromisoformat(f'{d}T{tm}');t['clock_offset_s']+=(want-now).total_seconds();t['pending_settings']={}
  t.update(shutdown=False,owner=False,pending=None,paused=False,pause_reason=None,restart_required=False);self.powered_since=self.time_s
 def status_light(self):
  """Light strip (manual p.3): white idle or processing, orange / blue user action on channel 1 / 2, green run completed, red error."""
  t=self.term
  if not self.powered or t['shutdown'] or not t['settings']['led_light']:return None
  if self.phase=='failed':return 'red'
  if t['pending'] is not None or t['paused'] or t['awaiting']:return 'orange' if self.selected==0 else 'blue'
  if self.phase=='succeeded' and self.stage=='finished_residual_solute':return 'green'
  return 'white'
 def _terminal_values(self):
  t=self.term;i=self.selected;lv=self._levels()
  return {'run.setups':[dict(copy.deepcopy(x),channel=k) for k,x in enumerate(t['setup'])],'run.issues':self._setup_issues(i),'run.estimate':self._estimate(i),'run.pending_sample_load':t['pending'],
          'run.paused':t['paused'],'run.pause_reason':t['pause_reason'],'run.hold':t['hold'],'run.awaiting':t['awaiting'],
          'solvents.inlets':[dict(solvent=x['solvent'],capacity_ml=x['capacity_ml'],level_ml=lv['inlets_ml'][k]) for k,x in enumerate(t['inlets'])],
          'waste.level':dict(capacity_ml=t['waste']['capacity_ml'],level_ml=lv['waste_ml']),'notices':self._notices(),'uv.zero_au':list(t['uv_zero']),
          'uv.calibration':copy.deepcopy(t['uv_cal']),'results.list':[dict({k:copy.deepcopy(v) for k,v in r.items() if k not in ('setup','history_from','operation_id','start_time_s','end_time_s')},status=self._run_status(r))
                                                                    for r in sorted(t['runs'],key=lambda r:not r['favorite'])],
          'library.columns':[copy.deepcopy(c) for _,c in sorted(t['columns'].items())],'library.solvents':[copy.deepcopy(c) for _,c in sorted(t['solvents'].items())],
          'library.racks':[copy.deepcopy(c) for _,c in sorted(t['racks'].items())],
          'library.users':[{k:(v is not None if k=='password' else v) for k,v in u.items()} for _,u in sorted(t['users'].items())],
          'settings':copy.deepcopy(t['settings']),'settings.pending':copy.deepcopy(t['pending_settings']),'owner.unlocked':t['owner'],'usb.files':list(t['usb']),
          'system.shutdown':t['shutdown'],'system.restart_required':bool(t.get('restart_required')),'system.datetime':self._clock(),
          'flush.state':None if t['flush'] is None else dict(kind=t['flush']['kind'],path=t['flush']['path'],left_ml=t['flush']['left']/ML),
          'status.light':self.status_light(),'truth.uv_error':copy.deepcopy(t['uv_err']),
          'rack.vessels_used':[int(sum(t['vessel_used'][k*RACK_SIZE:(k+1)*RACK_SIZE])) for k in range(3)],'rack.type':self._rack_type(i)}
 def _freeze_runs(self):
  for r in self.term['runs']:
   if r['operation_id']==self.operation_id and 'final' not in r:r['final']=self._run_status(r);r['end_time_s']=self.time_s
 # ---------------------------------------------------------------- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 def panel_read(self):
  out=self.read('agent');out.update({'host.powered':bool(self.powered and not self.term['shutdown']),'host.power_epoch':self.power_epoch,'host.switch_on':self.powered,
                                     'host.running':self.phase=='running','host.channel_busy':self.phase=='running' and self.stage not in ('standby',),'host.powered_since':self.powered_since})
  return out
 def panel_invoke(self,capability_id,arguments):
  """A panel action calls the same command a direct call would.  A gesture made through a session acts as that session's caller: the session's
  ownership guard applies (ending, stopping or switching off another agent's run or flush is refused) and operations started from the screen
  (run, equilibration, flushes, prime, purge) belong to the caller (the session records the owner of a new operation)."""
  try:
   if self._caller is not None:BiotageSelektSession(self,self._caller)._guard(capability_id,arguments)
   return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _pinvoke(self,capability,a):
  validate(PANEL_CAPS[capability]['input_schema'],a)
  try:
   if capability=='panel.touch':
    to=None if a.get('to_x') is None or a.get('to_y') is None else (int(a['to_x']),int(a['to_y']))
    out=dict(accepted=bool(self.panel.touch(a['display_id'],int(a['x']),int(a['y']),float(a.get('duration_s',.1)),to=to)))
   elif capability=='panel.switch':out=dict(accepted=bool(self.panel.switch(a['input_id'],a['position'])))
   elif capability=='panel.press':out=dict(accepted=bool(self.panel.press(a['input_id'],float(a.get('duration_s',.1)))))
   elif capability=='panel.frame':return dict(frame=self.panel.frame())
   else:
    import hashlib,tempfile
    data,w,hh=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
    d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True)
    path=d/f'{a["display_id"]}_{sha[:16]}.png'
    if not path.exists():path.write_bytes(data)
    return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=hh,sha256=sha))
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  self.revision+=1;return out
 def _fail(self,code):self.phase='failed';self.fault=code;self.flow=0.;self.fraction=0
 def _advance(self,dt):
  self.flow=0.;self.fraction=0
  if self.phase!='running':return
  if self.term['paused'] or self.stage=='gradient_completed':return               # paused (arm home, pump stopped) or waiting for the operator
  if self.term['flush'] is not None:return self._flush_advance(dt)
  i=self.selected;self.elapsed_s+=dt;m=self.methods[i]
  if self.stage=='purging':
   self.pressure[i]*=math.exp(-dt/self.plant['release_tau_s'])
   if self.pressure[i]<1.:
    self.pressure[i]=0.
    if self.term.get('after_purge'):                                            # end of the equilibration: Sample Load dialog (or back to idle)
     what,ph=self.term['after_purge'];self.term['after_purge']=None;self.phase=ph if ph!='running' else 'idle';self.stage='standby'
     if what=='sample_load':self.term['pending']=i;self.stage='sample_load'
     return
    if not self._next_cleanup(i):self.phase='succeeded';self.stage='finished_residual_solute';self.done[i]=bool(m and self.gradient_s[i]>=m['gradient'][-1]['time_s']-1e-8)
   return
  if self.elapsed_s-self.term['held_s']>m['timeout_s']:self._fail('TIMEOUT');return
  c=self.columns[i];q=m['flow_m3_s'];phi=self._target_phi();pair=c['solvent_pair'];supply=np.zeros(4);mod=m.get('modifier');fm=mod['fraction'] if mod else 0.
  supply[pair[0]]=q*(1-phi)*(1-fm);supply[pair[1]]=q*phi*(1-fm)
  if mod:supply[mod['inlet']]+=q*fm                                              # fixed modifier percentage in the mix (manual p.9)
  cv=c['void_volume_m3']/N;vm=self.plant['mixer_volume_m3'];vd=self.plant['detector_volume_m3']
  resistance=c['resistance_pa_s_m3']*(1+(c['strong_solvent_resistance_factor']-1)*float(self.colfluid[i,pair[1]].sum()/c['void_volume_m3']))
  if m.get('pressure_cap_pa') and q*resistance>m['pressure_cap_pa']:supply*=m['pressure_cap_pa']/resistance/q;q=m['pressure_cap_pa']/resistance   # column Max Pressure
  self.pressure[i]=q*resistance
  if self.pressure[i]>min(3e6,c['pressure_limit_pa']):self._fail('OVERPRESSURE');return
  raw=self.epsilon@self.detmol/vd*self.plant['optical_path_m']*self.plant['uv_response_scale'];self.term['uv_raw']=[float(x) for x in raw];self.uv=self._uv_reading(raw)
  mask=np.array(m.get('collect_mask',[True,True]))                                 # UV1 / UV2 used for collection (Collect) or only shown
  want=m['collection_mode']=='all' or bool(mask.any() and float(np.max(self.uv[mask]))>=m['threshold_absorbance'])
  if want and self.tube>=self.rack['positions']:self._collection_full();return
  target=(.018*(self.tube%8),.018*(self.tube//8)) if want else (0.,.33);distance=math.hypot(target[0]-self.x,target[1]-self.y);h=min(dt,m['gradient'][-1]['time_s']-self.gradient_s[i],.2*min(vm,vd,cv)/q)
  for k in range(4):
   if supply[k]>0:h=min(h,self.reservoir[k]/supply[k])
  collecting=want and distance<1e-9
  free=min(m['fraction_volume_m3'],self.rack['vessel_volume_m3'])-self.fractionfluid[self.tube].sum() if collecting else self.plant['waste_capacity_m3']-self.wastefluid.sum();h=min(h,max(0.,free)/q)
  if h<=1e-12:
   if any(self.reservoir[k]<=1e-15 and supply[k]>0 for k in range(4)):self._fail('SOLVENT_EMPTY')
   elif collecting:self.tube+=1;self.elapsed_s-=dt;return 0.
   else:self._fail('WASTE_FULL')
   return
  if distance>0:
   f=min(1.,self.plant['collector_speed_m_s']*h/distance);self.x+=f*(target[0]-self.x);self.y+=f*(target[1]-self.y)
  self.moving=distance>1e-9
  mixout=self.mixer/vm*q*h;colout=self.colfluid[i]/cv*q*h;detout=self.detfluid/vd*q*h
  strong=self._strong(i,cv);k=self.k0[i,:,None]*np.exp(-self.slope[i,:,None]*strong[None,:]);soluteout=self.colmol[i]/(cv*(1+k))*q*h;detmolout=self.detmol/vd*q*h
  self.reservoir=np.maximum(0.,self.reservoir-supply*h);self.mixer+=supply*h-mixout;self.colfluid[i]-=colout;self.colfluid[i,:,0]+=mixout;self.colfluid[i,:,1:]+=colout[:,:-1];self.detfluid+=colout[:,-1]-detout
  self.colmol[i]-=soluteout;self.colmol[i,:,1:]+=soluteout[:,:-1];self.detmol+=soluteout[:,-1]-detmolout
  if collecting:
   self.fractionfluid[self.tube]+=detout;self.fractionmol[self.tube]+=detmolout;self.fraction=self.tube+1;self.term['vessel_used'][self.tube]=True
   if self.fractionfluid[self.tube].sum()>=min(m['fraction_volume_m3'],self.rack['vessel_volume_m3'])-1e-15:self.tube+=1
  else:self.wastefluid+=detout;self.wastemol+=detmolout
  self.flow=q
  if self.term['hold']:self.term['held_s']+=h                                       # isocratic Hold: the gradient clock stops
  else:self.gradient_s[i]+=h
  self.elapsed_s-=dt-h
  if self.gradient_s[i]>=m['gradient'][-1]['time_s']-1e-9 and not self.term['hold']:self._gradient_end(i)
  elif any(self.reservoir[k]<=1e-15 and supply[k]>0 for k in range(4)):self._fail('SOLVENT_EMPTY')
  elif collecting and self.tube>=self.rack['positions']:self._collection_full()
  elif self.wastefluid.sum()>=self.plant['waste_capacity_m3']-1e-15:self._fail('WASTE_FULL')
  return h
 def _strong(self,i,cv):
  """Elution-strength coordinate of the column cells: the strong-solvent volume fraction, plus a modifier weighted on the A -> B strength scale."""
  return (self.colfluid[i,self.columns[i]['solvent_pair'][1]]+self.modw[i]@self.colfluid[i])/cv
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
  remaining=float(dt_s)
  while remaining>1e-10:
   d=min(.1,remaining)
   if self.phase=='running' and self.stage=='gradient':
    i=self.selected;d=min(d,.2*min(self.plant['mixer_volume_m3'],self.plant['detector_volume_m3'],self.columns[i]['void_volume_m3']/N)/self.methods[i]['flow_m3_s'])
   used=self._advance(d);d=d if used is None else used;self.time_s+=d;self.revision+=1;remaining-=d
   if self.plant and self.phase!='running':self.uv=self._uv_reading(self._uv_raw())   # the detector reads also between runs (zero / offset / residue)
   if self.term['settings']['solvent_monitoring'] or self.term['settings']['waste_monitoring']:self._monitor()
   self._jump();self._valley()
   if self.fraction>0 and self.term['runs'] and self.term['runs'][-1]['operation_id']==self.operation_id and self.fraction-1 not in self.term['runs'][-1]['tubes']:
    self.term['runs'][-1]['tubes'].append(self.fraction-1)                   # vessels of this run (Rack panel, Analysis view)
   if self.time_s>=self.next_record:
    self.history.append(dict(time_s=self.time_s,channel=self.selected,elapsed_s=float(self.gradient_s[self.selected]),uv1=float(self.uv[0]),uv2=float(self.uv[1]),pressure_pa=float(self.pressure[self.selected]),strong_fraction=self._target_phi(),fraction_number=self.fraction,stage=self.stage));self.next_record=self.time_s+1.
ALL_CAPS=list(CAPS)+list(TCAPS)+list(PANEL_CAPS)                            # instrument commands, touch-screen software, front panel


def _cap_vis(k):
    return CAPS[k][5] if k in CAPS else TCAPS[k][5] if k in TCAPS else PANEL_CAPS[k]['visibility']


def _cap_in(k):
    return CAPS[k][0] if k in CAPS else TCAPS[k][0] if k in TCAPS else PANEL_CAPS[k]['input_schema']


# ---------------------------------------------------------------- contract metadata of the touch-screen software and the panel commands
_PROC=['cycle.phase','cycle.stage','cycle.fault','cycle.elapsed_s','flow.actual_m3_s','pressure.gauge_pa','channel.c0.pressure_pa','channel.c1.pressure_pa',
       'gradient.mixer_fraction','uv.absorbance_1','uv.absorbance_2','collection.fraction_number','collection.positions_used','flush.state','status.light']
_TRUTH=[k for k in SLOTS if k.startswith('truth.') and k!='truth.uv_error']
_SETUP=['run.setups','run.issues','run.estimate']
TEFFECTS={'run.setup':_SETUP+['flow.target_m3_s'],'column.scan':_SETUP,'run.prepare':['run.pending_sample_load','channel.selected','cycle.phase','cycle.stage','flush.state','status.light'],
          'run.unprepare':['run.pending_sample_load','cycle.stage','status.light'],
          'run.begin':['cycle.phase','cycle.stage','run.pending_sample_load','results.list','uv.zero_au','channel.c0.method_ready','channel.c1.method_ready','flow.target_m3_s','status.light'],
          'run.pause':['run.paused','run.pause_reason','collector.x_m','collector.y_m','status.light'],'run.resume':['run.paused','run.pause_reason','status.light'],
          'run.end':['cycle.phase','cycle.stage','run.paused','run.pause_reason','run.hold','run.awaiting','flush.state','status.light'],'run.hold':['run.hold'],
          'run.next_vessel':['collection.positions_used'],'run.uv_zero':['uv.zero_au','uv.absorbance_1','uv.absorbance_2'],'run.extend':['run.awaiting','cycle.stage'],
          'solvents.setup':['solvents.inlets','run.issues'],'waste.setup':['waste.level','run.issues'],
          'solvents.prime':['cycle.phase','cycle.stage','flush.state','status.light'],'flush.start':['cycle.phase','cycle.stage','flush.state','status.light'],
          'flush.air':['cycle.phase','cycle.stage','flush.state','status.light'],'purge.start':['channel.selected','cycle.phase','cycle.stage','status.light'],
          'results.update':['results.list'],'results.export':['usb.files'],'results.create_run':_SETUP,'results.delete':['results.list'],
          'owner.login':['owner.unlocked'],'owner.logout':['owner.unlocked'],'columns.save':['library.columns'],'columns.delete':['library.columns'],
          'solvents.save':['library.solvents'],'solvents.delete':['library.solvents'],'racks.set_volume':['library.racks'],
          'users.save':['library.users','results.list','run.setups'],'users.delete':['library.users','results.list','run.setups'],
          'settings.set':['settings','settings.pending'],'database.backup':['usb.files','system.restart_required'],
          'database.restore':['results.list','library.columns','library.solvents','library.racks','library.users','system.restart_required'],
          'logs.export':['usb.files'],'uv.calibrate':['uv.calibration','uv.zero_au'],'sensor.configure_error':['truth.uv_error'],
          'system.shutdown':['system.shutdown','owner.unlocked','run.pending_sample_load','status.light'],
          'rack.reinsert':['rack.vessels_used','collection.positions_used','run.issues','truth.fractions_amount_mol'],'rack.empty':['rack.vessels_used','run.issues']}
TON_STEP={k:_PROC+_TRUTH+['solvents.inlets','waste.level','notices','run.paused','run.pause_reason','run.awaiting','run.pending_sample_load','results.list']
          for k in ('run.prepare','run.begin','run.end','run.resume','run.extend','solvents.prime','flush.start','flush.air','purge.start')}
TASYNC={'run.prepare','run.begin','solvents.prime','flush.start','flush.air','purge.start'}
_G=['POWER_OFF']
TERRORS={'run.setup':_G+['BUSY','UNKNOWN_COLUMN','UNKNOWN_SOLVENT','UNKNOWN_USER'],'column.scan':_G+['BUSY','UNKNOWN_COLUMN'],
         'run.prepare':_G+['BUSY','SETUP_INCOMPLETE','PRECONDITION_FAILED','SOLVENT_EMPTY'],'run.unprepare':[],
         'run.begin':_G+['NOT_WAITING','NO_SAMPLE','PRECONDITION_FAILED','METHOD_ENVELOPE','REFERENCE_RANGE','UNKNOWN_REFERENCE'],
         'run.pause':_G+['NOT_RUNNING','PAUSED'],'run.resume':_G+['NOT_PAUSED','COLLECTION_FULL'],'run.end':_G+['NOT_RUNNING','NOT_EQUILIBRATING'],
         'run.hold':_G+['NOT_RUNNING'],'run.next_vessel':_G+['NOT_COLLECTING','COLLECTION_FULL'],'run.uv_zero':_G+['SETTING_DISABLED','NOT_RUNNING'],
         'run.extend':_G+['NOT_RUNNING'],'solvents.setup':_G+['UNKNOWN_SOLVENT','LEVEL_ABOVE_CAPACITY'],'waste.setup':_G+['LEVEL_ABOVE_CAPACITY'],
         'solvents.prime':_G+['NO_SOLVENT','PRECONDITION_FAILED','BUSY','SOLVENT_EMPTY'],
         'flush.start':_G+['PERCENT_SUM','UNKNOWN_COLUMN','PRECONDITION_FAILED','BUSY','SOLVENT_EMPTY'],
         'flush.air':_G+['VOLUME_REQUIRED','UNKNOWN_COLUMN','PRECONDITION_FAILED','BUSY'],'purge.start':_G+['BUSY','NO_PRESSURE','PRECONDITION_FAILED'],
         'results.update':_G+['UNKNOWN_RUN','TOO_MANY_PICTURES'],'results.export':_G+['UNKNOWN_RUN','BUSY'],'results.create_run':_G+['UNKNOWN_RUN','BUSY'],
         'results.delete':_G+['OWNER_REQUIRED','UNKNOWN_RUN','BUSY'],'owner.login':_G+['OWNER_REQUIRED','WRONG_PASSWORD'],'owner.logout':[],
         'columns.save':_G+['OWNER_REQUIRED','READ_ONLY','NAME_EXISTS'],'columns.delete':_G+['OWNER_REQUIRED','UNKNOWN_COLUMN','READ_ONLY','IN_USE'],
         'solvents.save':_G+['OWNER_REQUIRED','READ_ONLY','NAME_EXISTS','IN_USE'],'solvents.delete':_G+['OWNER_REQUIRED','UNKNOWN_SOLVENT','READ_ONLY','IN_USE'],
         'racks.set_volume':_G+['OWNER_REQUIRED','UNKNOWN_RACK','VOLUME_ABOVE_DEFAULT'],'users.save':_G+['OWNER_REQUIRED','LAST_OWNER','NAME_EXISTS','READ_ONLY'],
         'users.delete':_G+['OWNER_REQUIRED','UNKNOWN_USER','LAST_OWNER'],'settings.set':_G+['OWNER_REQUIRED'],'database.backup':_G+['OWNER_REQUIRED','BUSY'],
         'database.restore':_G+['OWNER_REQUIRED','BUSY','NO_BACKUP'],'logs.export':_G+['OWNER_REQUIRED'],'uv.calibrate':_G+['OWNER_REQUIRED','BUSY'],
         'sensor.configure_error':[],'system.shutdown':_G+['BUSY'],'rack.reinsert':['PRECONDITION_FAILED','BUSY','UNKNOWN_RACK'],
         'rack.empty':_G+['PRECONDITION_FAILED','BUSY','UNKNOWN_RACK']}
ERR_ZH={'POWER_OFF':'未通电或已关机','BUSY':'系统处理中','OWNER_REQUIRED':'需要System Owner登录','WRONG_PASSWORD':'密码错误','UNKNOWN_COLUMN':'未知柱类型',
        'UNKNOWN_SOLVENT':'未知溶剂','UNKNOWN_USER':'未知用户','SETUP_INCOMPLETE':'运行参数缺失或错误（警告按钮列出）','PRECONDITION_FAILED':'前提不满足（柱、管架、托盘、残压）',
        'NOT_WAITING':'没有等待上样的运行','NO_SAMPLE':'柱上没有样品','NOT_RUNNING':'没有进行中的纯化','PAUSED':'已暂停','NOT_PAUSED':'未暂停',
        'NOT_EQUILIBRATING':'不在平衡阶段','NOT_COLLECTING':'当前没有收集','COLLECTION_FULL':'分配的管架没有空管','SETTING_DISABLED':'系统设置中未开启手动UV归零',
        'SOLVENT_EMPTY':'溶剂不足','LEVEL_ABOVE_CAPACITY':'液量大于容量','NO_SOLVENT':'进液口未指定溶剂','PERCENT_SUM':'溶剂百分比之和须为100 %',
        'VOLUME_REQUIRED':'须选择柱类型或输入体积','UNKNOWN_RUN':'未知结果','TOO_MANY_PICTURES':'报告图片已达上限','READ_ONLY':'预置Biotage条目只能隐藏',
        'NAME_EXISTS':'名称已存在','IN_USE':'条目正被使用','UNKNOWN_RACK':'未知管架类型','VOLUME_ABOVE_DEFAULT':'容器体积只能调低','LAST_OWNER':'至少保留一个System Owner',
        'NO_BACKUP':'U盘上没有该备份','NO_PRESSURE':'没有需要释放的压力','METHOD_ENVELOPE':'梯度方法超出范围','REFERENCE_RANGE':'波长超出参考光谱范围',
        'UNKNOWN_REFERENCE':'无参考光谱','INVALID_ARGUMENT':'参数无效','UNKNOWN_INSTANCE':'未知实例','UNKNOWN_CAPABILITY':'未知能力','ACCESS_DENIED':'无权限',
        'REQUEST_CONFLICT':'请求ID冲突'}
PANEL_EFFECTS={'panel.touch':sorted({x for k in TCAPS for x in TEFFECTS[k] if k!='sensor.configure_error'}|{'power.enabled'}),
               'panel.switch':['power.enabled','system.shutdown','owner.unlocked','cycle.phase','status.light','system.datetime'],'panel.capture':[],'panel.frame':[]}


def _terminal_cap_meta(key,slots):
    """Contract entry of a touch-screen command or a panel command."""
    if key in TCAPS:
        inp,out,example,components,kind,vis,meaning=TCAPS[key];fx=TEFFECTS[key];errs=TERRORS[key]
    else:
        d=PANEL_CAPS[key];inp,out,example,components,kind,vis,meaning=d['input_schema'],d['output_schema'],d['example_arguments'],d['component_ids'],d['kind'],d['visibility'],d['function_zh']
        fx=PANEL_EFFECTS[key];errs=[]
    async_=key in TASYNC
    errors=[dict(code=e,meaning_zh=ERR_ZH.get(e,e),side_effects='none') for e in dict.fromkeys(['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','REQUEST_CONFLICT']+errs)]
    if async_:errors+=[dict(code=e,meaning_zh=ERR_ZH.get(e,e)+'（过程中）',side_effects='partial') for e in ['OVERPRESSURE','SOLVENT_EMPTY','WASTE_FULL','TIMEOUT'] if e not in errs]
    effects=[dict(state_id=k,timing='immediate',description_zh='触屏软件命令立即改变的设定或状态') for k in fx if k in slots]
    effects+=[dict(state_id=k,timing='on_step',description_zh='之后的step中按过程模型演化（平衡、梯度、冲洗、卸压、监控）') for k in TON_STEP.get(key,[]) if k in slots and k not in fx]
    query=kind=='query' or key in ('panel.capture','panel.frame')
    return dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=list(components),availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
                implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
                binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.biotage_selekt.BiotageSelektSession.invoke',
                             invocation=f'session.invoke(instance_id="selekt_01",capability_id={key!r},arguments={example!r},request_id="unique")'),
                preconditions=[] if query else [dict(description_zh='按用户手册的软件规则检查（通电、未在处理、System Owner登录、参数完整等）；被拒时状态不变',
                                                     state_ids=['power.enabled','cycle.phase','owner.unlocked'],enforced_by='runtime',
                                                     check=f'BiotageSelektRuntime.{"_t_"+key.replace(".","_") if key in TCAPS else "_pinvoke"}')],
                state_effects=effects,observes=[],model_effects=dict(mode='none',description_zh='触屏软件层：只改变运行层状态，没有几何联动',binding_ids=[]),
                execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
                               completion_criterion='启动后由之后的step推进到cycle.phase不再为running' if async_ else '同步：成功时一次写入全部效果',
                               completion_state_ids=['cycle.phase','cycle.stage'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',
                               retry='same_request_id',resources=['touchscreen'] if not async_ else ['touchscreen','shared_pump_detector_collector','selected_column'],
                               partial_failure_zh='被拒时状态不变；过程中的故障保留已发生的转移' if async_ else '被拒时状态不变'),
                errors=errors,example_arguments=copy.deepcopy(example),verification=[],
                limitations=['触屏软件按Biotage Selekt User Manual（2020）建模；画面为平面控件，提示文字为自拟；网络/邮件/Spektra许可/外接检测器不在配置内。'],
                source_ids=['manufacturer_manual','simulation_assumption'])


_CONTROL_GUARDED={'acquisition.stop','run.end','run.pause','run.resume','run.hold','run.next_vessel','run.uv_zero','run.extend','run.setup',
                  'rack.empty'}       # commands that stop, end or change another agent's run or flush (taking a rack off and the left-side power switch
                                      # are physical: never refused; switching off acts as a power loss)


class BiotageSelektSession:
    """Host registers principals; public calls have no visibility override."""
    def __init__(self,host,principal_id): self._host=host; self._principal=principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta=export_biotage_selekt_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid'),sample_time_s=h.time_s,sample_window_s=None) for k in ids})

    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        try:
            return self._invoke(instance_id=instance_id,capability_id=capability_id,arguments=arguments,request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,
                status='rejected',state_revision=self._host.revision,clock={'domain':'simulation','time_s':self._host.time_s},
                result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or capability_id not in ALL_CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and _cap_vis(capability_id)!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(_cap_in(capability_id),arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        self._guard(capability_id,arguments); before=h.operation_id; h._caller=self._principal
        try: result=h.invoke(capability_id,**arguments)
        finally: h._caller=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        if h.operation_id and h.operation_id!=before and h.operation_id not in h._owners: h._owners[h.operation_id]=self._principal   # started by a panel gesture or run.prepare
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def _guard(self,capability_id,arguments):
        """An agent may not stop, end, switch off or change another agent's running operation (direct call or panel: pause / resume / End, Hold,
        next vessel, UV zero, Extend, live run-setup edits of that channel, Rack panel Empty); unowned operations and the evaluator are not restricted.
        Taking a rack off and putting it back (rack.reinsert) and the power switch on the left side (power.set) are physical actions and are not
        refused: the instrument's own rules apply (racks only with the run paused; switching off is a power loss)."""
        h=self._host
        if self._role!='agent' or capability_id not in _CONTROL_GUARDED or h.phase!='running': return
        if capability_id=='power.set' and (arguments.get('enabled') or not h.powered): return
        if capability_id=='run.setup' and arguments.get('channel')!=h.selected: return
        if h._owners.get(h.operation_id) not in (None,self._principal): raise ActionRejected('ACCESS_DENIED')

    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]: raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)

    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('chromatography.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_biotage_selekt_contract_metadata():
    slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(meaning_zh=SLOT_ZH[key],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BiotageSelektSession.read(state_ids=[{key!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    caps={}
    for key,(inp,out,example,components,kind,vis,meaning) in CAPS.items():
        async_=key in ('acquisition.start','pressure.release')
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.biotage_selekt.BiotageSelektSession.invoke',invocation=f'session.invoke(instance_id="selekt_01",capability_id={key!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='参数、资源与前提由本机invoke分支执行；是教学流程，不代表厂家互锁',state_ids=['cycle.phase'],enforced_by='runtime',check='BiotageSelektRuntime.invoke capability-specific guards')],state_effects=[] if kind=='query' else [dict(state_id=k,timing='on_step' if async_ else 'immediate',description_zh='仅按实际代码改变相应配置、库存、压力和收集状态；过程守恒') for k in slots],observes=[],model_effects=dict(mode='none',description_zh='运行层声明；几何同步在合同构建时依据实际验证登记',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='梯度计时完成再卸压；柱与检测池残留不清零' if key=='acquisition.start' else ('选中柱表压降至零，库存保留' if async_ else '同步配置或纯查询，不推进时钟'),completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['selected_column','shared_pump_detector_collector','principal_request_cache'],partial_failure_zh='取消/耗尽/满收集/压力超限保留已发生转移和残压。去重仅当前进程，非硬件安全认证。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','LOAD_ENVELOPE','METHOD_ENVELOPE']],example_arguments=copy.deepcopy(example),verification=[],limitations=['代理只读理想UV观测，真实组成限evaluator；文件隔离需另行部署。'],source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
        if async_:caps[key]['errors'] += [dict(code=e,meaning_zh=e,side_effects='partial') for e in ['TIMEOUT','OVERPRESSURE','SOLVENT_EMPTY','WASTE_FULL','COLLECTION_FULL']]
    narrow={'tray.set_extension':['tray.extension_m'],'rack.configure':['rack.configured'],'power.set':['power.enabled','cycle.phase','flow.actual_m3_s','collection.fraction_number','system.shutdown','owner.unlocked','run.pending_sample_load','run.paused','run.pause_reason','run.hold','run.awaiting','flush.state','status.light','system.datetime','settings.pending'],'acquisition.stop':['cycle.phase','flow.actual_m3_s','collection.fraction_number','run.paused','run.pause_reason','run.hold','run.awaiting','flush.state','status.light'],'channel.select':['channel.selected','cycle.phase','cycle.stage','cycle.fault','cycle.elapsed_s','cycle.gradient_s','pressure.gauge_pa','flow.target_m3_s','gradient.target_fraction','gradient.mixer_fraction']}
    for key,ids in narrow.items():caps[key]['state_effects']=[e for e in caps[key]['state_effects'] if e['state_id'] in ids]
    for key in list(TCAPS)+list(PANEL_CAPS):caps[key]=_terminal_cap_meta(key,slots)
    mechanism=dict(component_ids=['pump','selector_valve','column_0','column_1','uv_cell','collector_x','collector_y','tray'],state_ids=list(slots),meaning_zh='两柱顺序共享泵、混合器、UV池与收集针；四载液和逐物种摩尔守恒',implementation='lumped',equations=['12well-mixed cells percolumn;linear dilute equilibrium k=k0*exp(-S*local_strong_fraction);k0,Sassumed;local_strong_fraction=(V_B+w_M*V_M)/V_cell, w_M=clip((eps0_M-eps0_A)/(eps0_B-eps0_A),0,10) for a run-setup modifier (assumption)','solute_out=cell_total_mol/(mobile_cell_volume*(1+k))*Q*dt;all outgoing amounts transferred once,nonnegative CFL<=0.2','carrier_out=cell_carrier_volume/mobile_cell_volume*Q*dt;4carrier species tracked across mixer/columns/detector/144tubes/waste','linear interpolation of method nodes givespump inletstrongfraction;a modifier takes a fixed fraction of the flow (A,B share the rest);finite mixedvolume createsgradientdelay','A_lambda=sum(epsilon_measured_reference(lambda)*n_detector/V_detector)*host_path*host_response_scale;clip0..6.4;no wavelengthextrapolation','reference epsilon[M^-1cm^-1]*0.1 -> SI m2/mol;originalsolventconditions retained;cross-solvent transfer is assumption','P_gauge=host_column_resistance(strong_fraction)*Q;overpressure check before transfer;cancel traps indicatedP;purge firstorder to0 withnegligiblecompressibility approximation','fraction threshold=max(UV signals set to Collect);volume cutoff (Max Fraction Volume, vessel volume);finite XY transit routes to waste;only settledneedle can collect','initial species=bothcolumns+detector+allfractions+waste;initialeachcarrier=reservoir+mixer+bothcolumns+detector+allfractions+waste','read doesnot advance time;hoststep clips at CFL,finite source/sink,methodend;manual tray hostcommands,automatic needle processauthority'],parameters={},source_ids=['manufacturer_spec','manufacturer_manual','experimental_uv','simulation_assumption'],limitations=['60experimentalabsorptionreferencesarenot60Selektchromatograms;no measuredretention,columnperformance,recovery calibration.','Eachreferenceoriginalsolventandunresolvedtemperature retained;hostmustaccept UV response transfer assumption,notabsolutequantitationinrealgradienteluents.','12cell linear equilibrium retention is low-order teaching approximation;no measuredplatecount,nonlinearcompetitiveadsorption,ionization,reaction,precipitationor thermodynamic solventmixing.','Sampleloadingisinstant host placementinto firstcell,notmeasured injectionplug;host capacity rejects overloaded samples rather than simulating physicalsaturation.','Four liquid reservoirs finite;volumeadditivityassumed andsolutevolume neglected.No externalhosegasvacuumorthermalnetwork.','Bothchannels sequentially sharepump/mixer/detector/collector;othercolumn remains stopped.Prepduringrunning notsupported.Shared detectorretainedsolute carriesover conservatively onnextchannel.','Binary pairs A/B or C/D (plumbed per column) with an optional fixed-percentage modifier (M); solvents C/D as further gradient solvents unsupported (not described in the manual); automatic normal/reversed phase conversion between channels = two bypass flushes of the previous and current strong solvent (27 mL each at 100 mL/min, assumption);column equilibration (Equil.) runs the initial mix through the column (equilibration length/flow/pressure of the column type), otherwise initialconditioning is hostprovided.','Only two UV wavelengths (UV1/UV2 Collect/Show/Hide) andall/threshold collection with valley fractionation (assumed detection rule), Hold, manual fractionation, manual/automatic UV zero, Auto Extend; Spektra (lambda-All, baseline correction, spectrum), ELSD and externaldetector unsupported.','Finite XY motion usesassumed speed;waste diverted duringtravel,notcalibrateddelay-tubing/valvetiming.PhysXcontact andmotioncollisionplanning absent.','P=R*Q isquasisteadyhydraulicproxy;canceltrapsP,firstorderpurge neglectscompressibility/expelledvolume. Notpressure controlleror safetyvalvecertification.','Gradient±2% andUV±5mAU aredocumentedclaims,notmodelcalibratederrorbounds.No realnoise/calibration/lampwarming.','144tube geometry3x48 documentedcapacity9mL (racks A, B, C; Start Collection and rack assignment modelled);onlythisracklayout,not3840mLbottlerack/extendedbed.racks are taken off and put back with rack.reinsert (run paused; emptied vessels leave the instrument), RFID clears racks of previous runs, the Rack panel Empty clears the others; a full rack set pauses the run.','Traymanualpose isnotmotorizedcapability;QR column scan is the robot skill column.scan (no optics);RFID rack detection reports the host rack type and which runs used a rack (no radio model; off: rack type by hand),roboticcolumnmount or high-pressuresealmechanics.','Selected23kg from2018singlebedbrochure;series23–25kgrange retained;localmass/inertia/friction unknown,URDF proxiesnotmeasured.','NativeSession identity/asynchronous tested separately fromMatterix ack;deploymentfile/process isolation notprovided.'])
    spec=importlib.util.spec_from_file_location('_selekt_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.PARAM_ZH.update({'mixer_volume_m3':'假设梯度混合器持液体积','detector_volume_m3':'假设检测池混合持液体积','uv_response_scale':'原参考溶剂到洗脱剂的假设光学响应比例','collector_speed_m_s':'假设XY收集针速度','release_tau_s':'假设卸压时间常数','void_volume_m3':'所选柱假设流动相持液体积','resistance_pa_s_m3':'假设柱液力流阻','strong_solvent_resistance_factor':'假设强溶剂相对流阻','max_sample_amount_mol':'宿主限定线性稀负载上限，非实测吸附容量','conditioned_strong_fraction':'宿主预平衡强溶剂比例','pressure_limit_pa':'所选柱压力限值，不能超越系统30bar'})
    for key in slots:
        if slots[key]['unit']=='Pa':slots[key]['reference_conditions']='相对plant.ambient_pa的表压；不是绝压。'
        if slots[key]['unit']=='m^3/s':slots[key]['reference_conditions']='宿主给定实验条件下液体实际体积流率；非气体标准量。'
    ann.annotate_export(SLUG,caps,slots,{'flash_chromatography':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE),'column.configure':(COLUMN,COLUMN_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'flash_chromatography':mechanism})
