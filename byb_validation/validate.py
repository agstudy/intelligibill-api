_validationIntervals = dict(
    supply_rate= [0, 5],
    flat_rate= [0.1, 0.55],
    b1_rate= [0, 0.55],
    b2_rate= [0.1, 0.55],
    b3_rate= [0.1, 0.55],
    balance_rate= [0.1, 0.55],
    tou_peak_rate= [0.1, 0.7],
    tou_off_peak_rate= [0.08, 0.55],
    flex_peak_rate= [0.1, 0.70],
    flex_shoulder_rate= [0.1, 0.55],
    flex_off_peak_rate=[0.08, 0.55],
    no_summer_b1_rate= [0.1, 0.55],
    no_summer_b2_rate= [0.1, 0.55],
    no_summer_b3_rate= [0.1, 0.55],
    summer_b1_rate= [0.1, 0.55],
    summer_b2_rate= [0.1, 0.55],
    summer_b3_rate= [0.1, 0.55],
    no_summer_bf_rate= [0.1, 0.5],
    summer_bf_rate= [0.1, 1],
    summer_flex_peak_rate= [0.1, 0.7],
    summer_flex_off_peak_rate= [0.08, 0.55],
    summer_flex_shoulder_rate= [0.1, 0.55],
    no_summer_flex_peak_rate= [0.1, 0.7],
    no_summer_flex_off_peak_rate= [0.08, 0.55],
    no_summer_flex_shoulder_rate= [0.1, 0.55],
    solar_export_rate= [0, 1],
    solar_pv_capacity= [1, 10],
    green_percent= [0, 100],
    discount_energy= [0, 100],
    discount_total= [0, 100],
    cl0_usage= [0, 100000],
    cl1_usage= [0, 100000],
    cl2_usage= [0, 100000],
    cl0_rate= [0, 0.55],
    cl1_rate= [0, 0.55],
    cl2_rate= [0, 0.55],
    scl0_rate= [0, 0.3],
    scl1_rate= [0, 0.3],
    scl2_rate= [0, 0.3]
);


def validate(bill):
    for k,v in bill.items():
       if k in _validationIntervals.keys():
           interval = _validationIntervals[k]
           if not interval[0]<= v <= interval[1]:
               return False, k
    return True, None


if __name__=='__main__':
    import json
    with open("/home/agstudy/parsed/origin.json","r") as f :
        agls = json.load(f)
        for k in agls:
            value, error = validate(k)
            print(value)
