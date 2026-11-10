class VRButton {
    static createButton(renderer) {
        const button = document.createElement('button');

        function showEnterVR() {
            let currentSession = null;

            async function onSessionStarted(session) {
                session.addEventListener('end', onSessionEnded);
                await renderer.xr.setSession(session);
                button.textContent = 'EXIT VR';
                currentSession = session;
            }

            function onSessionEnded() {
                currentSession.removeEventListener('end', onSessionEnded);
                button.textContent = 'ENTER VR';
                currentSession = null;
            }

            button.style.display = '';
            button.style.cursor = 'pointer';
            button.style.left = 'calc(50% - 70px)';
            button.style.width = '140px';
            button.textContent = 'ENTER VR';

            button.onmouseenter = function () {
                button.style.opacity = '1.0';
            };
            button.onmouseleave = function () {
                button.style.opacity = '0.85';
            };

            button.onclick = function () {
                if (currentSession === null) {
                    const sessionInit = { optionalFeatures: ['local-floor', 'bounded-floor', 'hand-tracking', 'layers'] };
                    navigator.xr.requestSession('immersive-vr', sessionInit).then(onSessionStarted);
                } else {
                    currentSession.end();
                }
            };
        }

        function disableButton() {
            button.style.display = '';
            button.style.cursor = 'auto';
            button.style.left = 'calc(50% - 75px)';
            button.style.width = '150px';
            button.onmouseenter = null;
            button.onmouseleave = null;
            button.onclick = null;
        }

        function showWebXRNotFound() {
            disableButton();
            button.textContent = 'DESKTOP 3D MODE';
        }

        function showVRNotAllowed(exception) {
            disableButton();
            console.warn('WebXR session check error:', exception);
            button.textContent = 'DESKTOP 3D MODE';
        }

        button.id = 'VRButton';
        button.style.position = 'absolute';
        button.style.bottom = '24px';
        button.style.padding = '12px 16px';
        button.style.border = '1px solid #00f0ff';
        button.style.borderRadius = '8px';
        button.style.background = 'rgba(0, 240, 255, 0.25)';
        button.style.color = '#ffffff';
        button.style.font = 'bold 13px -apple-system, monospace';
        button.style.letterSpacing = '1px';
        button.style.textAlign = 'center';
        button.style.opacity = '0.85';
        button.style.outline = 'none';
        button.style.zIndex = '999';
        button.style.boxShadow = '0 0 16px rgba(0, 240, 255, 0.4)';
        button.style.transition = 'all 0.2s';

        if ('xr' in navigator) {
            button.id = 'VRButton';
            button.style.display = 'none';

            navigator.xr.isSessionSupported('immersive-vr').then(function (supported) {
                supported ? showEnterVR() : showWebXRNotFound();
            }).catch(showVRNotAllowed);
            return button;
        } else {
            showWebXRNotFound();
            return button;
        }
    }
}
window.VRButton = VRButton;
