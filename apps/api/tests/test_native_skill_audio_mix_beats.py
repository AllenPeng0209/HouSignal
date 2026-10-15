"""Physical dry/wet and nonuniform onset regression; no model or cloud calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import wave

import numpy as np
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "reports" / "skill-acceptance" / "fixtures"
ONSET_ORACLE = np.array([0.25,0.83,1.52,2.07,2.89,3.46,4.14])
TIER2_FILTER = "highpass=f=70,afftdn=nr=24:nf=-30:tn=1,anlmdn=s=0.0005"


def module():
    path = ROOT / "apps" / "api" / "app" / "content_skills" / "tools" / "av.py"
    spec=importlib.util.spec_from_file_location("audio_dry_wet_beat_target",path)
    target=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
    return target


def ffmpeg(*arguments):
    process=subprocess.run(["ffmpeg","-nostdin","-v","error",*map(str,arguments)],capture_output=True)
    assert process.returncode==0,process.stderr.decode(errors="replace")
    return process.stdout


def pcm(path):
    with wave.open(str(path)) as file:
        sample_rate,channels,frames=file.getframerate(),file.getnchannels(),file.getnframes()
        samples=np.frombuffer(file.readframes(frames),dtype="<i2").reshape(-1,channels).astype(np.float64)
    return samples,sample_rate,channels


def dispatch(tmp_path,skill,assets,parameters):
    result=module().dispatch(skill,input_text="",assets=[{"path":p} for p in assets],parameters=parameters,output_dir=tmp_path,context={})
    assert result.get("status")!="setup",result
    assert result["artifact_paths"]
    for path in result["artifact_paths"]:
        assert path.is_file() and path.stat().st_size>0
        assert path.resolve().is_relative_to(tmp_path.resolve())
    return result["artifact_paths"]


@pytest.fixture(scope="module")
def independent_wet(tmp_path_factory):
    output=tmp_path_factory.mktemp("physical-reference")/"wet.wav"
    # Independent FFmpeg reference: 1102 FFT + 353 nonlocal lookahead samples.
    aligned=f"apad=pad_len=1455,{TIER2_FILTER},atrim=start_sample=1455:end_sample=177855,asetpts=PTS-1455/SR/TB"
    ffmpeg("-y","-i",FIXTURES/"noisy_audio.wav","-af",aligned,"-c:a","pcm_s16le",output)
    return pcm(output)


@pytest.mark.parametrize("mix",[0,0.5,1])
def test_tier2_mix_is_real_dry_wet_linear_blend(tmp_path,independent_wet,mix):
    source=FIXTURES/"noisy_audio.wav"
    dry,rate,channels=pcm(source)
    wet,wet_rate,wet_channels=independent_wet
    output=dispatch(tmp_path,"audio-denoise",[source],{"tier":2,"mix":mix,"format":"wav"})[0]
    actual,out_rate,out_channels=pcm(output)
    assert rate==wet_rate==out_rate==44100 and channels==wet_channels==out_channels==2
    assert actual.shape==dry.shape==wet.shape
    expected=dry*(1-mix)+wet*mix
    assert np.max(np.abs(actual-expected))<=2
    if mix==0:
        assert np.array_equal(actual,dry)


def test_tier2_reduces_known_noise_against_independent_clean_signal(tmp_path):
    source=FIXTURES/"noisy_audio.wav"
    dry,rate,channels=pcm(source)
    output=dispatch(tmp_path,"audio-denoise",[source],{"tier":2,"mix":1,"format":"wav"})[0]
    wet,_,_=pcm(output)
    region=slice(rate,len(dry)-rate)
    improvements=[]
    # Spectral SNR is invariant to the existing filter's phase/lookahead delay.
    # Literal signal frequencies come from the independent fixture generator.
    for channel,tones in enumerate(((440,880),(660,1320))):
        def powers(data):
            samples=data[region,channel]
            power=np.abs(np.fft.rfft(samples*np.hanning(len(samples))))**2
            frequencies=np.fft.rfftfreq(len(samples),1/rate)
            wanted=np.zeros_like(frequencies,dtype=bool)
            for frequency in tones:wanted|=np.abs(frequencies-frequency)<8
            return power[wanted].sum(),power[~wanted].sum()
        before_signal,before_noise=powers(dry)
        after_signal,after_noise=powers(wet)
        assert after_signal/before_signal>0.9  # Do not earn SNR by deleting the signal.
        improvements.append(float(10*np.log10((after_signal/after_noise)/(before_signal/before_noise))))
    print("KNOWN_SIGNAL_SNR_IMPROVEMENT_DB",improvements)
    assert min(improvements)>3


def test_mix_point8_does_not_cancel_clean_signal_or_worsen_raw_error(tmp_path):
    source=FIXTURES/"noisy_audio.wav"
    dry,rate,channels=pcm(source)
    t=np.arange(len(dry))/rate
    clean=np.stack((.18*np.sin(2*np.pi*440*t)+.05*np.sin(2*np.pi*880*t),.15*np.sin(2*np.pi*660*t)+.04*np.sin(2*np.pi*1320*t)),axis=1)*32767
    output=dispatch(tmp_path,"audio-denoise",[source],{"tier":2,"mix":0.8,"format":"wav"})[0]
    actual,out_rate,out_channels=pcm(output)
    assert actual.shape==dry.shape and out_rate==rate and out_channels==channels
    region=slice(rate,3*rate)
    gains=[]
    for channel,frequencies in enumerate(((440,880),(660,1320))):
        for frequency in frequencies:
            time=t[region]
            def coefficient(samples):
                return 2*np.mean(samples[region,channel]*np.sin(2*np.pi*frequency*time))+1j*2*np.mean(samples[region,channel]*np.cos(2*np.pi*frequency*time))
            gains.append(float(abs(coefficient(actual)/coefficient(clean))))
    before=np.mean((dry[region]-clean[region])**2,axis=0)
    after=np.mean((actual[region]-clean[region])**2,axis=0)
    improvement=10*np.log10(before/after)
    print("MIX_POINT8_ACTUAL_TONE_GAINS",gains,"RAW_CLEAN_MSE_IMPROVEMENT_DB",improvement.tolist())
    assert min(gains)>=0.95 and max(gains)<=1.05
    # No alignment or fitted phase/gain adjustment is allowed in this metric.
    assert min(improvement)>3


def test_video_denoise_blend_retains_picture_duration_and_stereo(tmp_path):
    video=tmp_path/"input.mp4"
    ffmpeg("-y","-f","lavfi","-i","color=c=red:s=96x64:r=30:d=4","-i",FIXTURES/"noisy_audio.wav","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",video)
    output=dispatch(tmp_path/"result","audio-denoise",[video],{"tier":2,"mix":0.5})[0]
    data=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(output)]))
    v=next(stream for stream in data["streams"] if stream["codec_type"]=="video")
    a=next(stream for stream in data["streams"] if stream["codec_type"]=="audio")
    assert (v["width"],v["height"])==(96,64)
    assert a["channels"]==2 and a["sample_rate"]=="44100"
    assert float(data["format"]["duration"])==pytest.approx(4,abs=0.04)
    assert abs(float(a["duration"])-float(v["duration"]))<0.04
    assert ffmpeg("-i",video,"-map","0:v","-c:v","copy","-f","md5","pipe:1")==ffmpeg("-i",output,"-map","0:v","-c:v","copy","-f","md5","pipe:1")
    ffmpeg("-xerror","-i",output,"-map","0:v","-map","0:a","-f","null","-")


def test_video_alignment_preserves_real_audio_start_gap(tmp_path):
    source=tmp_path/"offset-input.mp4"
    ffmpeg("-y","-f","lavfi","-i","color=c=red:s=96x64:r=30:d=6","-itsoffset","1","-i",FIXTURES/"noisy_audio.wav","-map","0:v","-map","1:a","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",source)
    output=dispatch(tmp_path/"aligned","audio-denoise",[source],{"tier":2,"mix":0.8})[0]
    def probe(path):
        return json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)]))
    before,after=probe(source),probe(output)
    original=next(s for s in before["streams"] if s["codec_type"]=="audio")
    changed=next(s for s in after["streams"] if s["codec_type"]=="audio")
    assert float(changed["start_time"])>0.9
    assert abs(float(changed["start_time"])-float(original["start_time"]))<0.05
    assert changed["channels"]==original["channels"]==2
    assert float(after["format"]["duration"])==pytest.approx(6,abs=0.04)


@pytest.mark.parametrize("rate",[11025,48000])
def test_denoise_alignment_uses_actual_input_rate_and_preserves_silence_gap(tmp_path,rate):
    t=np.arange(rate*2)/rate
    active=((t>=0.2)&(t<0.7))|((t>=1.0)&(t<1.7))
    signal=np.stack((.15*np.sin(2*np.pi*440*t)*active,.12*np.sin(2*np.pi*660*t)*active),axis=1)
    source=tmp_path/"stereo-gap.wav"
    with wave.open(str(source),"wb") as file:
        file.setnchannels(2);file.setsampwidth(2);file.setframerate(rate)
        file.writeframes(np.rint(signal*32767).astype("<i2").tobytes())
    output=dispatch(tmp_path/"processed","audio-denoise",[source],{"tier":2,"mix":0.8,"format":"wav"})[0]
    actual,out_rate,channels=pcm(output)
    assert out_rate==rate and channels==2 and len(actual)==len(t)
    # The middle 0.2s of the real 0.3s pause remains silent in both channels.
    assert np.max(np.abs(actual[round(.75*rate):round(.95*rate)]))<=3
    for start,end,frequencies in ((.3,.6,(440,660)),(1.1,1.4,(440,660))):
        region=(t>=start)&(t<end)
        for channel,frequency in enumerate(frequencies):
            def coefficient(samples):
                values=samples[region,channel]
                return 2*np.mean(values*np.sin(2*np.pi*frequency*t[region]))+1j*2*np.mean(values*np.cos(2*np.pi*frequency*t[region]))
            reference=np.rint(signal*32767)
            assert abs(coefficient(actual)/coefficient(reference))>0.95


@pytest.fixture
def images(tmp_path):
    paths=[]
    for index,color in enumerate(("red","blue")):
        path=tmp_path/f"image-{index}.png"
        Image.new("RGB",(96,64),color).save(path)
        paths.append(path)
    return paths


def steady_tone(tmp_path):
    path=tmp_path/"steady.wav"
    ffmpeg("-y","-f","lavfi","-i","sine=frequency=440:d=2",path)
    return path


def test_automatic_beats_rejects_music_without_actual_onsets(tmp_path,images):
    music=steady_tone(tmp_path)
    with pytest.raises(ValueError,match="节拍|音头|beat_mode"):
        module().dispatch("beat-sync-video",input_text="",assets=[{"path":p} for p in [*images,music]],parameters={"size":"96x64","every":1},output_dir=tmp_path/"result",context={})
    assert not list((tmp_path/"result").glob("*.mp4"))


def test_auto_beat_dependency_gap_returns_setup_without_fake_video(tmp_path,images,monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,"numpy",None)
    result=module().dispatch("beat-sync-video",input_text="",assets=[{"path":p} for p in [*images,FIXTURES/"rhythm.wav"]],parameters={"size":"96x64","beat_mode":"auto"},output_dir=tmp_path/"result",context={})
    assert result["status"]=="setup" and "NumPy" in result["response"]
    assert result["artifact_paths"]==[]


def test_explicit_equal_interval_is_declared_base_and_not_automatic(tmp_path,images):
    music=steady_tone(tmp_path)
    paths=dispatch(tmp_path/"result","beat-sync-video",[*images,music],{"size":"96x64","beat_mode":"equal-interval","fallback_interval":0.5,"every":1,"effect":"none","kenburns":False})
    manifest=json.loads(paths[1].read_text())
    assert manifest["beat_mode"]=="equal-interval" and manifest["commercial_scope"]=="base"
    assert manifest["detector"]=="equal-interval-explicit" and manifest["fallback_used"] is False
    assert manifest["detected_onsets"]==[]
    assert [segment["start"] for segment in manifest["segments"]]==[0,0.5,1,1.5]


def test_auto_beats_detects_real_nonuniform_onsets_and_video_cut_times(tmp_path,images):
    # Oracle is used only for assertions; execution never receives beat times.
    paths=dispatch(tmp_path/"result","beat-sync-video",[*images,FIXTURES/"rhythm.wav"],{"size":"96x64","beat_mode":"auto","every":1,"effect":"none","kenburns":False})
    manifest=json.loads(paths[1].read_text())
    measured=np.asarray(manifest["detected_onsets"])
    assert manifest["source_onsets"]==measured.tolist()
    assert manifest["onset_evidence"]["detected_onset_count"]==len(measured)
    assert manifest["onset_evidence"]["calibrated_confidence"] is False
    assert manifest["beat_mode"]=="auto" and manifest["detector"]=="pcm-energy-onset"
    assert manifest["fallback_used"] is False
    assert len(measured)==len(ONSET_ORACLE)
    onset_error=np.max(np.abs(measured-ONSET_ORACLE))
    assert onset_error<0.04 and np.std(np.diff(measured))>0.06
    frames=np.frombuffer(ffmpeg("-i",paths[0],"-vf","scale=1:1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"),dtype=np.uint8).reshape(-1,3)
    color=frames[:,0]>frames[:,2]
    cut_times=(np.nonzero(color[1:]!=color[:-1])[0]+1)/30
    assert len(cut_times)==len(ONSET_ORACLE)
    cut_error=np.max(np.abs(cut_times-ONSET_ORACLE))
    print("ACTUAL_DETECTED_ONSETS",measured.tolist(),"MAX_ONSET_ERROR",float(onset_error),"ACTUAL_VIDEO_CUTS",cut_times.tolist(),"MAX_CUT_ERROR",float(cut_error))
    assert cut_error<0.04
    assert len(frames)==144  # 4.8s @30fps, without cumulative clip rounding drift.
    ffmpeg("-xerror","-i",paths[0],"-map","0:v","-map","0:a","-f","null","-")
