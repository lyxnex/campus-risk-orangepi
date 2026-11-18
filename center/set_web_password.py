"""Set/reset the webpage password locally, without changing device upload keys."""
import argparse
import getpass
from pathlib import Path
from common import load_json,atomic_json
from web_auth import password_hash

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path(__file__).with_name('center_config.json'))
    a=p.parse_args()
    first=getpass.getpass('New webpage password: ')
    if first!=getpass.getpass('Repeat password: '):p.error('Passwords do not match')
    try:encoded=password_hash(first)
    except ValueError as e:p.error(str(e))
    config=load_json(a.config);config['web_password_hash']=encoded
    atomic_json(a.config,config)
    print('Web password saved. Restart the PC center to apply; device upload keys are unchanged.')

if __name__=='__main__':main()
