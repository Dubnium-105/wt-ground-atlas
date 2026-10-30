#!/usr/bin/env python3
"""Run opt-in statistics calibration; failure produces a report, never changes physics."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.data import read_json,canonical
from wt_atlas.forecast.statistics import calibrate

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--observations',type=Path,required=True);p.add_argument('--config',type=Path,default=ROOT/'config/forecast.json');p.add_argument('--out',type=Path,default=ROOT/'reports/statistics-calibration.json');a=p.parse_args()
    try:r=calibrate(read_json(a.observations),read_json(a.config)['statistics'])
    except (ValueError,OSError,KeyError) as exc:r={'status':'uncalibrated','blocked':str(exc),'automatically_accepted':False}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(canonical(r),encoding='utf-8');print(canonical(r))

if __name__=='__main__':main()
