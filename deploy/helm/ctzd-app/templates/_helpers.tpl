{{- define "ctzd-app.labels" -}}
app.kubernetes.io/name: ctzd-app
app.kubernetes.io/instance: {{ .Values.fullname }}
app.kubernetes.io/part-of: ctzd
ctzd.contoso.com/sbu: {{ .Values.sbu }}
ctzd.contoso.com/app: {{ .Values.app }}
{{- end -}}

{{- define "ctzd-app.selector" -}}
app.kubernetes.io/name: ctzd-app
app.kubernetes.io/instance: {{ .Values.fullname }}
{{- end -}}
