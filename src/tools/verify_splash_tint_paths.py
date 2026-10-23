"""Reuse the real hidden PC harness to prove coloured batch/Rectangle paths.

The rect arm fails only coloured splash context construction after installation;
neck and flare installation is untouched. This is drawing-path verification,
not Android, audio, screen-FPS or visual acceptance.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/tools'))
import verify_short_grain_runtime as harness
from release_decision import source_digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path',choices=('batch','rect'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--width',type=int,default=540)
    parser.add_argument('--height',type=int,default=924)
    args=parser.parse_args()
    output=args.output.resolve();cases=args.cases.resolve()
    expected=source_digest()
    report=dict(completed=False,requested_path=args.path,
        actual_path='ColoredSplashBatch' if args.path=='batch' else 'coloured_rectangles',
        force_build_failure=args.path=='rect',source_sha256=expected,
        scope='Actual hidden PC drawing path; no visual, device or FPS acceptance',
        draw_calls=0,nonempty_draw_calls=0,max_live_splashes=0,rect_pool_max=0,
        observed_paths=[],all_draws_verified=False,
        context_build_failures=0,install_was_batch=False,
        adjacent_paths=dict(neck_batch_draws=0,flare_batch_draws=0,
                            neck_classes=[],flare_classes=[]))
    original_load=harness.load_module

    def loaded(name,path):
        module=original_load(name,path)
        if name!='pc_material_runtime':return module
        splash=sys.modules['flow_splash_experiment']
        cls=module.HourglassWidget
        report['install_was_batch']=getattr(cls,'splash_renderer',None)=='batch'
        if not report['install_was_batch']:
            raise AssertionError('Coloured splash installation did not complete before path injection')
        if args.path=='rect':
            def unavailable():
                report['context_build_failures']+=1
                raise RuntimeError('synthetic coloured splash build failure for path verification')
            splash._tint_context=unavailable
        draw=cls._draw_splashes
        def checked(widget):
            result=draw(widget)
            batch=getattr(widget,'_splash_batch',None)
            if args.path=='batch':
                if not isinstance(batch,splash.ColoredSplashBatch):
                    raise AssertionError('Requested coloured batch did not actually draw')
            else:
                if batch is not None:
                    raise AssertionError('Requested ordinary coloured Rectangles still use a batch')
                if widget._sn:
                    pool=widget._splash_rects
                    if len(pool)<widget._sn or len(widget._splash_tint_colors)<widget._sn:
                        raise AssertionError('Ordinary coloured Rectangle pool is incomplete')
                    if any(not isinstance(rect,module.Rectangle) for rect in pool[:widget._sn]):
                        raise AssertionError('Ordinary splash pool does not contain real Rectangles')
                    if any(not isinstance(color,module.Color)
                           for color in widget._splash_tint_colors[:widget._sn]):
                        raise AssertionError('Ordinary splash pool does not contain real Colors')
                    report['rect_pool_max']=max(report['rect_pool_max'],len(pool))
            report['draw_calls']+=1
            report['nonempty_draw_calls']+=int(widget._sn>0)
            report['max_live_splashes']=max(report['max_live_splashes'],int(widget._sn))
            actual=type(batch).__name__ if batch is not None else 'coloured_rectangles'
            if actual not in report['observed_paths']:report['observed_paths'].append(actual)
            for key,attribute in [('neck_batch_draws','_neck_batches'),('flare_batch_draws','_flare_batches')]:
                value=getattr(widget,attribute,None)
                report['adjacent_paths'][key]+=int(bool(value))
                class_key='neck_classes' if key=='neck_batch_draws' else 'flare_classes'
                name=type(value).__name__ if value is not None else 'None'
                if name not in report['adjacent_paths'][class_key]:
                    report['adjacent_paths'][class_key].append(name)
            return result
        cls._draw_splashes=checked
        return module

    harness.load_module=loaded
    saved_argv=sys.argv
    try:
        sys.argv=['verify_short_grain_runtime.py','--runtime',str(ROOT),'--output',str(output),
            '--renderer','texture','--width',str(args.width),'--height',str(args.height),
            '--cases',str(cases)]
        harness.main()
        capture=json.loads((output/'result.json').read_text(encoding='utf-8'))
        if not capture['completed'] or not capture['passed'] or not capture['source_unchanged']:
            raise AssertionError('Underlying path capture is incomplete')
        if not report['nonempty_draw_calls'] or not report['max_live_splashes']:
            raise AssertionError('No nonempty splash drawing was observed')
        if args.path=='rect' and not report['context_build_failures']:
            raise AssertionError('Ordinary path did not exercise the intended build failure')
        if source_digest()!=expected:
            raise AssertionError('Runtime sources changed during path verification')
        report.update(source_files=capture['source_files'],source_files_end=capture['source_files_end'],
            frame_count=len(capture['frames']),frame_names=[f['name'] for f in capture['frames']],
            frame_sha256={f['file']:f['sha256'] for f in capture['frames']},
            all_draws_verified=True,completed=True)
    finally:
        harness.load_module=original_load
        sys.argv=saved_argv
        output.mkdir(parents=True,exist_ok=True)
        (output/'splash-path.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('SPLASH_TINT_PATH_OK',args.path,report['frame_count'],report['max_live_splashes'])


if __name__=='__main__':main()
