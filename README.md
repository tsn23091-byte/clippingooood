# clipping app

Streamlit で動く記事クリッピング整理アプリです。

## ローカル実行

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## ファイル構成

- `app.py`: Streamlit アプリ本体
- `keyword_config.toml`: キーワード設定
- `requirements.txt`: 依存ライブラリ

キーワードの追加・修正は `app.py` ではなく `keyword_config.toml` を編集します。

## GitHub に載せるとき

このフォルダをそのままリポジトリに追加してください。

最低限必要なファイル:

- `app.py`
- `keyword_config.toml`
- `requirements.txt`
- `README.md`

## Streamlit Community Cloud でのデプロイ

1. GitHub にこのプロジェクトを push
2. Streamlit Community Cloud にログイン
3. `New app` を選択
4. GitHub リポジトリを選択
5. Main file path に `app.py` を指定
6. Deploy

## 補足

- `pywin32` は Windows で `.msg` 出力を使うときだけ必要です
- Streamlit Community Cloud は Linux のため、`requirements.txt` では Windows のときだけ `pywin32` を入れる設定にしています
