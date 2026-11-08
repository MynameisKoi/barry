Shader "Barry/Environment/Planet"
{
    Properties
    {
        _LightColor ("Light", Color) = (0.725,0.651,1,1)
        _MidColor ("Mid", Color) = (0.357,0.247,0.851,1)
        _DarkColor ("Dark", Color) = (0.118,0.086,0.376,1)
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" "RenderPipeline"="UniversalPipeline" }
        Cull Off
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            struct Attributes { float4 positionOS : POSITION; float2 uv : TEXCOORD0; };
            struct Varyings { float4 positionCS : SV_POSITION; float2 uv : TEXCOORD0; };
            CBUFFER_START(UnityPerMaterial)
                half4 _LightColor, _MidColor, _DarkColor;
            CBUFFER_END
            Varyings Vert(Attributes input)
            {
                Varyings output;
                output.positionCS = TransformObjectToHClip(input.positionOS.xyz);
                output.uv = input.uv;
                return output;
            }
            half4 Frag(Varyings input) : SV_Target
            {
                half3 lower = lerp(_DarkColor.rgb, _MidColor.rgb, saturate(input.uv.y * 2.0));
                half3 color = lerp(lower, _LightColor.rgb, saturate((input.uv.y - 0.5) * 2.0));
                return half4(color, 1.0);
            }
            ENDHLSL
        }
    }
}
