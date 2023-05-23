"""tutorial.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1yA1Lexs9XxXqs34p79hDEPoOnHiOJMSU

# 睡眠段階予測コンペティション チュートリアル
コンペティションでは睡眠ポリグラフ検査によるセンサーデータを利用して睡眠段階を予測します。  

このノートブックでは、睡眠ポリグラフについての簡単な説明とチュートリアルベースラインを紹介します。

このチュートリアルはpythonで脳波を解析するためのライブラリである`MNE`のチュートリアルを参考にしています

https://mne.tools/stable/auto_tutorials/clinical/60_sleep.html#sphx-glr-auto-tutorials-clinical-60-sleep-py

# 1.睡眠ポリグラフ検査
睡眠ポリグラフ検査とは、睡眠関連疾患の診断に用いられる検査の一つです。  
睡眠ポリグラフは、ポリソムノグラフィ(polysomnography: PSG)と呼ばれ、睡眠時における脳波、呼吸、脚の運動、あごの運動、眼球運動（レム睡眠とノンレム睡眠）、心電図、酸素飽和度、胸壁の運動、腹壁の運動などを記録します。  
  
PSGの多くは、睡眠時無呼吸症候群を主体とする、睡眠呼吸障害の診断と治療効果判定のために行われています。潜在患者数は約300万人と推定されている一方で、検査に使用する機器の利用や、睡眠段階を判別するのに高度な訓練が必要となるので、検査需要に追いつていないと指摘があります。[1]

# 2.睡眠段階
睡眠段階の判定は睡眠ポリグラフを解析し、**30秒を1epoch**として、epoch毎に睡眠段階を判定します。  
1epochに異なる睡眠段階が混在する場合は、最も多くの時間を占める睡眠段階を採用します。  

今回使用するデータセットである、`Sleep-EDF Database Expanded`では、RechtschaffenとKales(R&K)による睡眠段階ガイドラインが使用され、睡眠段階を以下の8つのラベルでラベル付けされています。
- Sleep stage W (覚醒状態)
- Sleep stage 1
- Sleep stage 2
- Sleep stage 3
- Sleep stage 4
- Sleep stage R (レム睡眠)
- Sleep stage ? (不明)
- Movement time

一方、米国睡眠医学会(AASM)によるガイドラインでは`Sleep stage 1 ~ 4`を3つに分類しています。[2]    

`NME`のチュートリアルや過去の研究では、R&Kによる睡眠段階分類の`Sleep stage 3`と`Sleep stage 4`を`Sleep stage 3/4`に変更して  
AASMによる睡眠段階分類に合わせています。  


このコンペティションでもその方法にならい、AASMによる分類のラベル付けに変更します。

`Movement time`と`Sleep stage ?`は`sample_submission.csv`の正解データには含まれていません
"""

# ラベル名をIDに置き換える
# Sleep stage 3とSleep stage 4を同じIDとして、AASMによる分類に変更する
RANDK_LABEL2ID = {
    'Movement time': -1,
    'Sleep stage ?': -1,
    'Sleep stage W': 0,
    'Sleep stage 1': 1,
    'Sleep stage 2': 2,
    'Sleep stage 3': 3,
    'Sleep stage 4': 3,
    'Sleep stage R': 4
}

# AASMによる分類
LABEL2ID = {
    'Movement time': -1,
    'Sleep stage ?': -1,
    'Sleep stage W': 0,
    'Sleep stage 1': 1,
    'Sleep stage 2': 2,
    'Sleep stage 3/4': 3,
    'Sleep stage R': 4
}
ID2LABEL = {v:k for k, v in LABEL2ID.items()}

"""# ライブラリのインストールとデータ設定"""

import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd
import numpy as np
import warnings
from tqdm.auto import tqdm

from sklearn.model_selection import GroupKFold
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, f1_score

import mne

DATA_DIR = Path("./input/")
EDF_DIR = DATA_DIR / "edf_data"

"""# sample submissionの確認
はじめにサブミッションの形式を確認します。

"""

sample_submission_df = pd.read_csv(DATA_DIR/"sample_submission.csv", parse_dates=[1])

sample_submission_df

"""meas_timeは睡眠段階の予測時間です。  
30秒を1epochとして睡眠段階を判断しているため、30秒毎に行があるのが分かります。

提供されているデータは波形データとアノテーションデータが別々になっているため  
この形式に処理する必要があります。

# レコードデータの読み込み
被験者のメタデータは`**_recoreds.csv`としてcsvで保存されています。
"""

train_record_df = pd.read_csv(DATA_DIR/"train_records.csv")
test_record_df = pd.read_csv(DATA_DIR/"test_records.csv")

train_record_df.head()

test_record_df.head()

"""各カラムの説明は以下となります

* id:行に一意のID
* subject_id:被験者にユニークなID
* night:測定した夜のID
* sex:性別
* hypnogram:アノテーションデータのファイル名
* psg:センサーデータのファイル名
"""

# 訓練とテストで被験者は重複していないようです
len(set(train_record_df["subject_id"].unique()) & set(test_record_df["subject_id"].unique()))

print("訓練被験者数", len(train_record_df["subject_id"].unique()))
print("テスト被験者数", len(test_record_df["subject_id"].unique()))

"""## 被験者データの表示

被験者(subject)のある晩(night)のデータを見てみましょう。  
`record type`が`PSG`であるものは、`睡眠段階を予測するための波形データ`に相当し、`Hypnogram`は`睡眠段階を記録したデータ`に相当します  

`edf`ファイルは医療時系列データの保存によく使用されるデータフォーマットになります。  
ロードするためには専用のライブラリが必要となります。

このノートブックでは、脳波などを解析する際に用いられるpythonライブラリである`NME`を使用します。  
"""

# パスを設定
train_record_df["hypnogram"] = train_record_df["hypnogram"].map(lambda x: str(EDF_DIR/x))
train_record_df["psg"] = train_record_df["psg"].map(lambda x: str(EDF_DIR/x))
test_record_df["psg"] = test_record_df["psg"].map(lambda x: str(EDF_DIR/x))

row = train_record_df.iloc[0]

"""### edfファイルの読み込み
`mne.io.read_raw_edf()`メソッドで`edf`ファイルを読み込むことができます。 

**患者一人データを読み込むとcsvで数万行になるため、メモリの対策が必要になります。** 

`NME`ではEDFをファイルを読み込む際に、`preload=False`を設定できます。  
データのメタ情報のみ読み込む機能であり、省メモリで済むようになります。

詳しいリファレンスは以下をご参考下さい。  

https://mne.tools/stable/generated/mne.io.read_raw_edf.html
"""

# edfファイルの読み込み
psg_edf = mne.io.read_raw_edf(row["psg"], preload=False)

# 読み込んだデータは、mne.io.edf.edf.RawEDFクラスのインスタンスになります
type(psg_edf)

# infoでメタ情報を表示できます
psg_edf.info

"""センサーチャンネルの名前を確認します"""

psg_edf.ch_names

"""それぞれのチャンネルは睡眠ポリグラフからのセンサーチャンネルの情報になっています。  

詳細についてはデータの公開先([Sleep-EDF Database Expanded](https://www.physionet.org/content/sleep-edfx/1.0.0/))をご参照下さい。

日本語でのセンサーの説明などは、参考文献にある睡眠検査[3]や終夜睡眠ポリグラフ[4]をなどを参考にして下さい。

ここでは、センサーチャンネルについて簡単に説明します。

- EEG： 脳波。すべての予測段階の判定に必要であり、意識水準と対応して変化する。
  - Fpz-Cz、Pz-Ozは頭に取り付けるセンサー箇所の箇所の電位差を表す
- EOG： 眼電図。 眼球運動を測定。
  - horizontalは水平方向に取り付けた電位差を表す
- EMG： オトガイ(顎下)筋電図。
- Resp oro-nasal：口鼻呼吸 (oroが口、nasalが鼻、respはrespirationの略で呼吸を表す)
- Temp rectal：直腸温 (原著に記載がないため、実際に直腸の温度を計測したかどうかは不明)
- Event marker：各時刻で起きたイベントの時刻

読み込んだデータは`to_data_frame`でdataframe化できます。
"""

psg_df = psg_edf.to_data_frame()

print(psg_df)

"""EEGとEOGは100Hzなので、0.00秒から始まっています。

従って、データの合計時間は(7890000/100)/3600=約22時間です。

計測時間の開始は`meas_date`で取得できます  
これを設定すると16時から翌14時過ぎまで計測していることが分かります
"""

meas_start = psg_edf.info["meas_date"]
meas_start = meas_start.replace(tzinfo=None)
# 100Hz
psg_df["meas_time"] = pd.date_range(start=meas_start, periods=len(psg_df), freq=pd.Timedelta(1 / 100, unit="s"))

print(psg_df)

"""## Hypnogramデータ(ラベル)の読み込み
Hypnogramには睡眠段階のラベルが保存されています
"""

# Hypnogramはread_annotationsで読み込むことが可能です
annot = mne.read_annotations(row["hypnogram"])

# 同様にto_data_frameでdataframe化できます
annot_df = annot.to_data_frame()

"""アノテーションは開始時間とその間隔で構成されています。  

"""

print(annot_df)

annot_df["description"].value_counts()

"""カラムの内容を簡単に説明します
- onset：経過時間（年と日付は設定が必要なので正しくありません。この例では時間の経過のみが正しいものになります）
- duration：アノテーションの間隔時間
- description：睡眠段階のラベル

最初と最後にかなり長い`Sleep Stage W`があることが分かります

## 波形データとアノテーションデータの紐づけ
波形データとアノテーションデータは別々のデータ構造を持っているため、波形データにアノテーションを紐付ける作業が必要になります。

睡眠段階は30秒を1epochとして判定されるため、波形データを30秒毎に区切って1epochとして、`onset`と`duration`から睡眠段階を紐付けます。  
これは煩雑な作業になりますが、`mne`には、この操作を行うメソッドがあります。  

また、前後にかなり長い`Sleep Stage W`があります。  
データ数を減らすため、適当に5時間で解析時間の切り捨てを行います

今回は処理を簡単にするために、チャンネルに`EEG Fpz-Cz`のみを利用します(`read_raw_edf`の`include`メソッドにEEGのみを指定)
"""

# 再度データを読み込みます
# 簡単のためにEEG Fpz-Czのみ利用します
psg_edf = mne.io.read_raw_edf(row["psg"], include=["EEG Fpz-Cz"], verbose=False)
annot = mne.read_annotations(row["hypnogram"])

# 1時間(3600秒)*5
truncate_start_point = 3600 * 5
truncate_end_point = (len(psg_edf)/100) - (3600 *5)
# 切り捨て
annot.crop(truncate_start_point, truncate_end_point, verbose=False)

# annotationデータを紐づけます
psg_edf.set_annotations(annot, verbose=False, emit_warning=False)

# 30秒毎に分割された睡眠段階
events, _ = mne.events_from_annotations(psg_edf, event_id=RANDK_LABEL2ID, chunk_duration=30., verbose=False)

# 30秒毎に1epochとする
tmax = 30. - 1. / psg_edf.info['sfreq']
epoch = mne.Epochs(raw=psg_edf, events=events, event_id=LABEL2ID, tmin=0, tmax=tmax, baseline=None, verbose=False, on_missing='ignore')

# subjectとnightの設定 
epoch.info["temp"] = {
    "id":row["id"],
    "subject_id":row["subject_id"],
    "night":row["night"],
    "truncate_start_point":truncate_start_point,
    "truncate_end_point":truncate_end_point
}

"""`mne.epochs.Epochs`クラスは波形データにラベルを紐づけたインスタンスになります"""

print(type(epoch))
print(epoch)

"""`RawEDF`クラスと同様にデータフレーム化できます。

データフレームを生成して確認します
"""

epoch_df = epoch.to_data_frame(verbose=False)
print(epoch_df)

"""`epoch`のカラムが追加されていることが分かります。  
`epoch`は0.00〜29.99秒の30秒で、1つのepochとなっていることが分かります。  
`time`はsampling rateが100Hzなので0.00秒になっています。

eventsのarrayは  
1列目が計測開始からの経過時間(10ms)  
3列目はアノテーションのID  
になっています（今回の例では2列目は特に考慮する必要がありません。詳しくは[ドキュメント](https://mne.tools/stable/glossary.html#term-events)をご参考下さい）
"""

print(events)

# epoch数と一致することが分かります
print(events.shape)

"""# 計測時間の設定

timeカラムは0.00〜29.99秒のエポック内の秒数になっており連続した測定時間になっていません。  
なので計測時間から連続した時間を振ります。


データにはMeasurement date(測定日)の情報がありますが、切り捨てをしたため測定日を計算し直す必要があります。  
具体的には、切り捨てが発生した`truncate_start_point`を`start`として測定日に設定します。  
終了時刻は、データ点の長さ分を100Hzの時間単位で計算すれば決まります。
"""

# 測定日を切り捨てが発生したポイントまでスライド
new_meas_date = epoch.info["meas_date"].replace(tzinfo=None) + datetime.timedelta(seconds=epoch.info["temp"]["truncate_start_point"])
# 連続した時間を作成
epoch_df["meas_time"] = pd.date_range(start=new_meas_date, periods=len(epoch_df), freq=pd.Timedelta(1 / 100, unit="s"))

print(epoch_df)

"""21時から翌の9時までの睡眠の波形データとアノテーションが紐付いたデータが作成されました。  
csvデータとして扱いたい場合、このdataframeを保存しておくとよいかもしれません。  


関数化しておきます。
"""

def epoch_to_df(epoch:mne.epochs.Epochs) -> pd.DataFrame:
    truncate_start_point = epoch.info["temp"]["truncate_start_point"]
    
    df = epoch.to_data_frame(verbose=False)
    
    new_meas_date = epoch.info["meas_date"].replace(tzinfo=None) + datetime.timedelta(seconds=truncate_start_point)
    
    df["meas_time"] = pd.date_range(start=new_meas_date, periods=len(df), freq=pd.Timedelta(1 / 100, unit="s"))
    
    return df

epoch_to_df(epoch)

"""## submission形式への変換
`epoch`毎にグループ化して`time`の最小値を取ることでsubmissionする形式に変換できます
"""

label_df = epoch_df.loc[epoch_df.groupby("epoch")["time"].idxmin()][["meas_time"]].reset_index(drop=True)
label_df["condition"] = "Sleep stage W"
label_df["id"] = epoch.info["temp"]["id"]
label_df

"""関数化しておきます"""

def epoch_to_sub_df(epoch_df:pd.DataFrame, id, is_train:bool) -> pd.DataFrame:
    cols = ["id", "meas_time"]
    if is_train:
        # 訓練セットの場合はラベルを追加
        cols.append("condition")
    
    label_df = epoch_df.loc[epoch_df.groupby("epoch")["time"].idxmin()].reset_index(drop=True)
    label_df["id"] = id
    
    return label_df[cols]

epoch_to_sub_df(epoch_df, epoch.info["temp"]["id"], is_train=True)

"""テストデータにはアノテーションファイルがないので`events`の行列を、先程の例で作成することはできません。  


評価の開始と終了時刻はsample submissionにあるので、前後の切り捨て時刻を計算して求めます。  
アノテーションのラベルIDは`0`としてダミーで生成します。
"""

test_row = test_record_df.iloc[0]
psg_edf = mne.io.read_raw_edf(test_row["psg"], include=["EEG Fpz-Cz"], verbose=False)

# PSGの開始をedfのmeas_dateから取得します
start_psg_date = psg_edf.info["meas_date"]
# 計算のためにtimezoneを消去します
start_psg_date = start_psg_date.replace(tzinfo=None)

# sample submissionから評価される時間レンジを取得します
test_start_time = sample_submission_df[sample_submission_df["id"]==test_row["id"]]["meas_time"].min()
test_end_time = sample_submission_df[sample_submission_df["id"]==test_row["id"]]["meas_time"].max()
# psgの計測開始、評価の対象の開始、評価の対象の終了
print(f"psg start: {start_psg_date},  test start: {test_start_time}, test end: {test_end_time}")

# psgの計測時間から評価の対象の開始と終了を経過時間(秒)になおす
truncate_start_point = int((test_start_time - start_psg_date).total_seconds())
truncate_end_point = int((test_end_time - start_psg_date).total_seconds())+30
print(f"event start sencond: {truncate_start_point}, event end second: {truncate_end_point} ")

# 30秒毎にデータ点を生成
event_range = list(range(truncate_start_point, truncate_end_point, 30))
events = np.zeros((len(event_range), 3), dtype=int)
events[:, 0] = event_range

# 秒を10m秒に変換する
events = events * 100

events

tmax = 30. - 1. / psg_edf.info['sfreq']
epoch = mne.Epochs(raw=psg_edf, events=events, event_id={'Sleep stage W': 0}, tmin=0, tmax=tmax, baseline=None, verbose=False)

epoch.to_data_frame()

"""trainとtestで以上の処理をすべて行えるように関数化しておきます"""

def read_and_set_annoation(record_df:pd.DataFrame, include=None, is_test=False) -> List[mne.epochs.Epochs]:
    whole_epoch_data = []

    for row_id, row in tqdm(record_df.iterrows(), total=len(record_df)):        
        # PSGファイルとHypnogram(アノテーションファイルを読み込む)
        psg_edf = mne.io.read_raw_edf(row["psg"], include=include, verbose=False)
        
        if not is_test:
            # 訓練データの場合
            annot = mne.read_annotations(row["hypnogram"])

            # 切り捨て
            truncate_start_point = 3600 * 5
            truncate_end_point = (len(psg_edf)/100) - (3600 *5)
            annot.crop(truncate_start_point, truncate_end_point, verbose=False)

            # アノテーションデータの切り捨て
            psg_edf.set_annotations(annot, emit_warning=False)
            events, _ = mne.events_from_annotations(psg_edf, event_id=RANDK_LABEL2ID, chunk_duration=30., verbose=False)
            
            event_id = LABEL2ID
        else:
            # テストデータの場合
            start_psg_date = psg_edf.info["meas_date"]
            start_psg_date = start_psg_date.replace(tzinfo=None)

            test_start_time = sample_submission_df[sample_submission_df["id"]==row["id"]]["meas_time"].min()
            test_end_time = sample_submission_df[sample_submission_df["id"]==row["id"]]["meas_time"].max()
            
            truncate_start_point = int((test_start_time - start_psg_date).total_seconds())
            truncate_end_point = int((test_end_time- start_psg_date).total_seconds())+30
            
            event_range = list(range(truncate_start_point, truncate_end_point, 30))
            events = np.zeros((len(event_range), 3), dtype=int)
            events[:, 0] = event_range
            events = events * 100
            
            event_id = {'Sleep stage W': 0}
            
    
        # 30秒毎に1epochとする
        tmax = 30. - 1. / psg_edf.info['sfreq']
        epoch = mne.Epochs(raw=psg_edf, events=events, event_id=event_id, tmin=0, tmax=tmax, baseline=None, verbose=False, on_missing='ignore')
        
        # 途中でデータが落ちてないかチェック
        assert len(epoch.events) * 30 == truncate_end_point - truncate_start_point
        
        # メタデータを追加
        epoch.info["temp"] = {
            "id":row["id"],
            "subject_id":row["subject_id"],
            "night":row["night"],
            "age":row["age"],
            "sex":row["sex"],
            "truncate_start_point":truncate_start_point
        }

        whole_epoch_data.append(epoch)

    return whole_epoch_data

# 処理を簡単にするためにEEG Fpz-Czのみ読み込みます
# またtrainをすべて処理するには少し時間がかかるため、ここでは半分ほどの50を利用することにします
train_record_subset_df = train_record_df.sample(n=50).reset_index(drop=True)

train_subset_epoch = read_and_set_annoation(train_record_subset_df, include=["EEG Fpz-Cz"], is_test=False)
test_whole_epoch = read_and_set_annoation(test_record_df, include=["EEG Fpz-Cz"], is_test=True)

"""# epochとlabelの関係の可視化"""

import plotly.graph_objects as go

sample_events = train_subset_epoch[0].events[:, 2]
sample_epoch_df = train_subset_epoch[0].to_data_frame(verbose=False)

go.Figure(
    data=[
        go.Scatter(x=sample_epoch_df["epoch"].unique(), y=list(map(lambda x: ID2LABEL[x], sample_events)))
    ],
    layout=go.Layout(
        yaxis=dict(title="sleep stage"),
        xaxis=dict(title="epoch"),
    )
)

"""直感的に分かるように、同じラベルが続いているのでepoch間のLabelには強い相関がありそうです。  
また、切り捨てを行いましたがそれでも前半に長い`Sleep Stage W`があります。切り捨て処理には工夫の余地がありそうです。

センサーデータがどのようになっているか確認してみます。  
すべてをプロットするにはデータが多すぎるため、epochの平均をとります。
"""

epoch_grouped_df = sample_epoch_df.groupby("epoch").agg({"EEG Fpz-Cz":"mean"}).reset_index()

epoch_grouped_df

go.Figure(
    data=[
        go.Scatter(x=epoch_grouped_df["epoch"], y=epoch_grouped_df["EEG Fpz-Cz"]),
    ],
    layout=go.Layout(
        yaxis=dict(title="EEG Fpz-Cz"),
        xaxis=dict(title="epoch"),
    )
)

"""中間では脳波の動きは小さく、前後で大きくなっています。  
起床しているときは脳波の動きが活発になっている印象があります

# 特徴量エンジニアリング
`MNE`ライブラリで紹介されている、パワースペクトル密度を使った方法を試してみます。

https://mne.tools/stable/auto_tutorials/clinical/60_sleep.html#feature-engineering
"""

def eeg_power_band(epochs):
    """EEG relative power band feature extraction.

    This function takes an ``mne.Epochs`` object and creates EEG features based
    on relative power in specific frequency bands that are compatible with
    scikit-learn.

    Parameters
    ----------
    epochs : Epochs
        The data.

    Returns
    -------
    X : numpy array of shape [n_samples, 5]
        Transformed data. 
    """
    # specific frequency bands
    FREQ_BANDS = {"delta": [0.5, 4.5],
                  "theta": [4.5, 8.5],
                  "alpha": [8.5, 11.5],
                  "sigma": [11.5, 15.5],
                  "beta": [15.5, 30]}

    spectrum = epochs.compute_psd(picks='eeg', fmin=0.5, fmax=30. ,verbose=False)
    psds, freqs = spectrum.get_data(return_freqs=True)
    # Normalize the PSDs
    psds /= np.sum(psds, axis=-1, keepdims=True)

    X = []
    for fmin, fmax in FREQ_BANDS.values():
        psds_band = psds[:, :, (freqs >= fmin) & (freqs < fmax)].mean(axis=-1)
        X.append(psds_band.reshape(len(psds), -1))

    return np.concatenate(X, axis=1)

train_df = []
for epoch in tqdm(train_subset_epoch):
    # 波形をdataframe化
    epoch_df = epoch_to_df(epoch)
    # submit形式のデータフレーム生成
    sub_df = epoch_to_sub_df(epoch_df, epoch.info["temp"]["id"], is_train=True)
    
    # パワースペクトル密度計算
    feature_df = pd.DataFrame(eeg_power_band(epoch))
    
    _df = pd.concat([sub_df, feature_df], axis=1)
    # 必要ないラベルがある場合は除外する
    _df = _df[~_df["condition"].isin(["Sleep stage ?", "Movement time"])]
    
    train_df.append(_df)
    
train_df = pd.concat(train_df).reset_index(drop=True)

train_df["condition"].value_counts()

# ラベルIDに変換
train_df["condition"] = train_df["condition"].map(LABEL2ID)

train_df

test_df = []
for epoch in tqdm(test_whole_epoch):
    # 波形をdataframe化
    epoch_df = epoch_to_df(epoch)
    # submit形式のデータフレーム生成
    sub_df = epoch_to_sub_df(epoch_df, epoch.info["temp"]["id"], is_train=False)

    # パワースペクトル密度計算
    feature_df = pd.DataFrame(eeg_power_band(epoch))
    
    _df = pd.concat([sub_df, feature_df], axis=1)
    
    test_df.append(pd.concat([sub_df, feature_df], axis=1))
    
test_df = pd.concat(test_df)

"""# 訓練

検証セットを作成します。  
約20％の被験者を検証セットに割り当てます。  
テストセットの分割のように、同じ被験者は検証セットに含まれないようにします。

ベースラインとしてランダムフォレストを使用します
"""

# 20％の被験者を選ぶ
val_size = int(train_record_df["subject_id"].nunique() * 0.20)
train_all_subjects = train_record_df["subject_id"].unique()
np.random.shuffle(train_all_subjects)

val_subjects = train_all_subjects[:val_size]
val_ids = train_record_df[train_record_df["subject_id"].isin(val_subjects)]["id"]

# 検証セットを作成します
trn_df = train_df[~train_df["id"].isin(val_ids)]
val_df = train_df[train_df["id"].isin(val_ids)]

trn_df

val_df

model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(trn_df.iloc[:, 3:], trn_df["condition"])

"""# 結果"""
print("\n==========モデルの結果を作成開始しました==========\n")

val_preds = model.predict(val_df.iloc[:, 3:])

score = accuracy_score(val_df["condition"], val_preds)
print("Accuracy Score："+score)

print(classification_report(val_df["condition"], val_preds,))

"""# テストの予測"""
print("\n==========テストの予測を開始しました==========\n")


test_df

test_preds = model.predict(test_df.iloc[:, 2:])

sample_submission_df["condition"] = test_preds

sample_submission_df["condition"] = sample_submission_df["condition"].map(ID2LABEL)

sample_submission_df

sample_submission_df["condition"].value_counts()

sample_submission_df.to_csv("submit.csv", index=False)
print("\n==========プログラムを完了しました==========\n")

"""#### 改善のヒント
* 利用するチャンネルを増やす
  * REM睡眠は眼球運動を伴います。眼の動きを記録したEOGチャンネルを取り入れることで精度向上の可能性があります
* 特徴量エンジニアリングを工夫する
  * 睡眠段階は前後のepochの睡眠段階と強い相関がありそうなので前後の特徴量を考慮できるようにする
  * 特徴量を自動生成してくれるライブラリ等を利用する
* 最先端の手法をためす
    * [Paper with Code](https://paperswithcode.com/sota/sleep-stage-detection-on-sleep-edf)にSleep EDF Expandedのスコアが掲載されています
    * 深層学習を利用したモデルなども提案されています

# 参考文献
[1] 八木 朝子,我が国における睡眠ポリグラフ検査（PSG）の現状,医学検査,Vol.65,1号,p1-11,2016,[doi:10.14932/jamt.15-70](https://doi.org/10.14932/jamt.15-70)  

[2] 加藤 久美,睡眠関連疾患の評価法,脳と発達,Vol.49,6号,p391-395,2017,[doi:10.11251/ojjscn.49.391](https://doi.org/10.11251/ojjscn.49.391)

[3] 野田 明子,睡眠検査,医学検査,Vol.66,J-STAGE-2号,p.95-105,2017,[doi:10.14932/jamt.17J2-13](https://doi.org/10.14932/jamt.17J2-13)  

[4] 野田 明子,古池 保雄,終夜睡眠ポリグラフ,生体医工学,Vol.46,2号,p.134-143,2008,[doi:10.11239/jsmbe.46.134](https://doi.org/10.11239/jsmbe.46.134)
"""