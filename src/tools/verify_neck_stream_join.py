"""Actual hidden-PC shader ablations for the local neck/stream material join.

This proves source scope, shared sampling and inactive-path preservation. It
does not approve appearance, motion continuity, Android, audio or frame rate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src/tools'))
import record_short_grain
import verify_short_grain_runtime as harness
from release_decision import source_digest

def remove_join_block(fragment):
    """Ablate only the current colour blend, retaining its geometry and texture."""
    pattern = (r'    if \(short_join_enabled > \.5 && depth < join_length\) \{\s*'
               r'float released=smoothstep\(short_join_overlap,join_length,depth\);\s*'
               r'rgb=mix\(neck_flow_rgb\(sand_position\),rgb,released\);\s*\}')
    value,count=re.subn(pattern,'',fragment)
    if count != 1:
        raise AssertionError('Expected exactly one current material join block')
    return value


def force_neck_helper(fragment):
    pattern = r'vec3 final_rgb\s*=\s*outlet_join\s*\?\s*neck_flow_rgb\(sand_position\)\s*:\s*mix\(original\.rgb,\s*flowing,\s*sand_mix\);'
    value, count = re.subn(pattern, 'vec3 final_rgb = neck_flow_rgb(sand_position);', fragment)
    if count != 1:
        raise AssertionError('Expected exactly one final neck RGB assignment')
    return value


def remove_join_guard(fragment):
    marker = 'short_join_enabled > .5 && '
    if fragment.count(marker) != 1:
        raise AssertionError('Expected exactly one inactive-path join guard')
    return fragment.replace(marker, '', 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=540)
    parser.add_argument('--height', type=int, default=924)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cases = output.parent/(output.name+'-cases.json')
    cases.write_text(json.dumps([dict(name='initial',period=50,elapsed=0,
                                      state='initial',frame_offsets=[0])]),encoding='utf-8')
    expected = source_digest()
    report = dict(completed=False, passed=False, visual_verdict=None, source_sha256=expected,
                  scope='Hidden PC shader scope and shared sampling only; not appearance, FPS or device acceptance',
                  ablation_mode='same-current-join-off',
                  frame_roles={'original-shader':'same-current-body with only colour join block removed',
                               'no-flow-original-shader':'same-current-body with only colour join block removed'},
                  tool_sha256=hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
                  seed=None, period=50, elapsed=8., frames=[], assertions=[])

    def save():
        output.mkdir(parents=True,exist_ok=True)
        (output/'neck-stream-join.json').write_text(
            json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

    def require(name, condition, **evidence):
        report['assertions'].append(dict(name=name,passed=bool(condition),**evidence))
        save()
        if not condition:
            raise AssertionError(name)

    def record(w, out, advance, capture, hidden_check):
        import numpy as np
        from PIL import Image
        game = sys.modules[w.__class__.__module__]
        helper = sys.modules['pc_material_probe_helpers']
        material = sys.modules['sand_flow_material']
        stream = sys.modules['short_grain_stream']
        ablation_body=remove_join_block(stream.FRAGMENT_BODY)
        report['ablation_body_sha256']=hashlib.sha256(ablation_body.encode()).hexdigest()
        report['seed'] = helper.SEED
        w.set_duration(50); w.reset(); w._rebuild_height_table()
        while w._warm_queue:
            w._warm_batches_step()
        random.seed(helper.SEED)
        np.random.seed(helper.SEED)
        w._front_seed=.75
        w.last_frame,w.last_tick=helper.EPOCH,None
        w.toggle(); advance(8.)
        state = helper.physical_state(w,game)
        report['physics'] = state
        require('stable_50_second_live_state', w.duration==50 and abs(w.elapsed-8)<1e-8
                and w.running and w.pn>0, particles=w.pn, elapsed_actual=w.elapsed)
        require('actual_short_and_neck_contexts', w._short_flow_context is not None
                and len(w._sand_flow_contexts)>=2)
        width,height=int(w.width),int(w.height)
        outlet=2*w._neck_y-w._taper['y_bot']
        image_outlet=height-outlet
        join_length=max(game.OUTLET_OVERLAP+.001,1.5*w._taper['t_in'])
        roi=[max(0,int(np.floor(w._cx-w._taper['t_in']-2))),
             max(0,int(np.floor(image_outlet))-1),
             min(width,int(np.ceil(w._cx+w._taper['t_in']+2))),
             min(height,int(np.ceil(image_outlet+join_length))+1)]
        neck_roi=[int(np.ceil(w._cx-.5*w._taper['t_in'])),
                  int(np.ceil(height-w._taper['y_bot']))+1,
                  int(np.floor(w._cx+.5*w._taper['t_in'])),int(np.floor(image_outlet))]
        report.update(dimensions=[width,height],outlet_y=image_outlet,join_length=join_length,
                      allowed_diff_roi=roi,neck_comparison_roi=neck_roi)

        def frame(name):
            hidden=hidden_check()
            before=helper.physical_state(w,game)
            w.redraw()
            path=out/(name+'.png');capture(path)
            after=helper.physical_state(w,game)
            shaders=helper.shader_state(w)
            report['frames'].append(dict(name=name,file=path.name,sha256=helper.file_hash(path),
                physics_before=before['sha256'],physics_after=after['sha256'],
                hidden=hidden,shaders=shaders,
                short_join_enabled=float(w._short_flow_context['short_join_enabled'])))
            require(name+'_physics_unchanged',before['sha256']==after['sha256']==state['sha256'])
            require(name+'_shaders_compiled',shaders['all_present_shaders_compiled'])
            with Image.open(path) as im:
                return np.asarray(im.convert('RGB')).copy()

        def set_fragment(context, fragment):
            context.shader.fs=fragment
            require('injected_fragment_compiled_'+str(len(report['assertions'])),context.shader.success,
                    fragment_sha256=hashlib.sha256(fragment.encode()).hexdigest())

        def compare(a,b,name,allowed=None,nonempty=False):
            changed=np.any(a!=b,axis=2)
            ys,xs=np.nonzero(changed)
            bbox=([int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1] if len(xs) else None)
            if allowed is None:
                require(name,not len(xs),changed_pixels=int(len(xs)),diff_bbox=bbox)
            else:
                outside=changed.copy();x0,y0,x1,y1=allowed;outside[y0:y1,x0:x1]=False
                require(name,not outside.any() and (bool(len(xs)) if nonempty else True),
                        changed_pixels=int(len(xs)),outside_pixels=int(outside.sum()),diff_bbox=bbox)

        current_fs=w._short_flow_context.shader.fs
        ctx=w._short_flow_context
        current=frame('current')
        require('normal_join_enabled',float(ctx['short_join_enabled'])==1.)
        set_fragment(ctx,material.GRAIN_FRAGMENT_HEADER+ablation_body)
        old=frame('original-shader')
        compare(current,old,'current_vs_join_off_only_local_outlet',roi,nonempty=True)
        set_fragment(ctx,current_fs)
        neck=w._sand_flow_contexts[1]
        require('neck_uv_debug_disabled',float(neck['sand_uv_debug'])==0.)
        neck_fs=neck.shader.fs
        set_fragment(neck,force_neck_helper(neck_fs))
        helper_image=frame('helper-neck')
        x0,y0,x1,y1=neck_roi
        require('neck_roi_nonempty',x1>x0 and y1>y0)
        compare(current[y0:y1,x0:x1],helper_image[y0:y1,x0:x1],
                'neck_shared_helper_pixel_exact_above_outlet')
        set_fragment(neck,neck_fs)

        saved=os.environ.get('HG_SAND_FLOW')
        try:
            os.environ['HG_SAND_FLOW']='0'
            w._build_dynamic_canvas()
            require('no_flow_rebuild_preserves_physics',
                    helper.physical_state(w,game)['sha256']==state['sha256'])
            require('no_flow_has_short_but_no_neck_context',w._short_flow_context is not None
                    and not w._sand_flow_contexts)
            ctx=w._short_flow_context
            inactive=frame('no-flow-current')
            require('no_flow_join_disabled',float(ctx['short_join_enabled'])==0.)
            current_fs=ctx.shader.fs
            set_fragment(ctx,material.GRAIN_FRAGMENT_HEADER+ablation_body)
            old_inactive=frame('no-flow-original-shader')
            compare(inactive,old_inactive,'inactive_current_equals_join_off_pixels')
            set_fragment(ctx,remove_join_guard(current_fs))
            negative=frame('no-flow-missing-guard-negative')
            compare(inactive,negative,'missing_guard_negative_is_detected_locally',roi,nonempty=True)
            set_fragment(ctx,current_fs)
        finally:
            if saved is None:os.environ.pop('HG_SAND_FLOW',None)
            else:os.environ['HG_SAND_FLOW']=saved
        report.update(callback_completed=True,hidden_at_callback_end=hidden_check())
        save()
        return dict(completed=True,scope=report['scope'],assertions=len(report['assertions']))

    saved_record,saved_argv=record_short_grain.record,sys.argv
    record_short_grain.record=record
    try:
        sys.argv=['verify_short_grain_runtime.py','--runtime',str(ROOT),'--output',str(output),
                  '--width',str(args.width),'--height',str(args.height),'--renderer','texture',
                  '--cases',str(cases),'--video']
        harness.main()
        captured=json.loads((output/'result.json').read_text(encoding='utf-8'))
        require('underlying_hidden_capture_complete',captured.get('completed') and captured.get('passed')
                and captured.get('source_unchanged'))
        require('runtime_source_identity_unchanged',source_digest()==expected)
        report.update(source_files=captured['source_files'],source_files_end=captured['source_files_end'],
                      hidden_at_creation=captured['hidden_at_creation'],hidden_at_finish=captured['hidden_at_finish'],
                      completed=True,passed=True)
    except BaseException as error:
        report['error']=dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc())
        raise
    finally:
        record_short_grain.record=saved_record
        sys.argv=saved_argv
        save()
    print('NECK_STREAM_JOIN_OK',len(report['frames']),len(report['assertions']))


if __name__=='__main__':
    main()
