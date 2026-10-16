"""Reproducible bounded mutation smoke test; full native fuzzing belongs in a VM."""
import argparse
import io
import json
from pathlib import Path
import random
import struct
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ape_pdf_studio.core import rpc_codec
from ape_pdf_studio.core.vector_protocol import validate
from ape_pdf_studio.core.worker_protocol import Messages


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=int,default=2000)
    parser.add_argument('--seed',type=int,default=76342)
    args=parser.parse_args()
    if not 1<=args.cases<=100000:raise SystemExit('cases must be 1..100000')
    rng=random.Random(args.seed);rejected=accepted=0;deadline=time.monotonic()+30
    corpus=[rpc_codec.dumps({'ok':True,'value':['text',12]}),b'{"result":{"pages":1}}\n',
            b'{"version":1,"size":[100,100],"layers":[]}']
    for index in range(args.cases):
        if time.monotonic()>deadline:raise SystemExit('Mutation budget exceeded')
        data=bytearray(rng.choice(corpus))
        for _ in range(rng.randint(1,8)):
            if data:data[rng.randrange(len(data))]=rng.randrange(256)
        try:
            if index%3==0:rpc_codec.read(io.BytesIO(data))
            elif index%3==1:list(Messages().feed(data+b'\n'))
            else:validate(json.loads(data))
            accepted+=1
        except (ValueError,TypeError,KeyError,IndexError,EOFError,RecursionError,UnicodeError,OverflowError):rejected+=1
    print(json.dumps({'cases':args.cases,'seed':args.seed,'accepted':accepted,'rejected':rejected}))


if __name__=='__main__':main()
