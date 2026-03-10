/*:
 * @plugindesc Shows a translation splash screen on game start.
 * Displays img/system/TranslationSplash.png with fade in/out.
 * Auto-injected by RPG Maker Translator. Click or press Enter to skip.
 *
 * @param FadeIn
 * @text Fade In (frames)
 * @type number
 * @default 40
 *
 * @param Wait
 * @text Wait (frames)
 * @type number
 * @default 180
 *
 * @param FadeOut
 * @text Fade Out (frames)
 * @type number
 * @default 30
 *
 * @author RPG Maker Translator
 */

(function() {
    "use strict";

    var params  = PluginManager.parameters("TranslationSplash");
    var FADE_IN  = Number(params["FadeIn"]  || 40);
    var WAIT     = Number(params["Wait"]    || 180);
    var FADE_OUT = Number(params["FadeOut"] || 30);

    var _splashDone = false;

    // ── Scene ────────────────────────────────────────────────

    function Scene_TranslationSplash() {
        this.initialize.apply(this, arguments);
    }

    Scene_TranslationSplash.prototype = Object.create(Scene_Base.prototype);
    Scene_TranslationSplash.prototype.constructor = Scene_TranslationSplash;

    Scene_TranslationSplash.prototype.initialize = function() {
        Scene_Base.prototype.initialize.call(this);
        this._waitTimer = WAIT;
        this._fadingOut = false;
        this._done = false;
    };

    Scene_TranslationSplash.prototype.create = function() {
        Scene_Base.prototype.create.call(this);
        // Load the pre-generated splash image from img/system/
        this._splashSprite = new Sprite(
            ImageManager.loadSystem("TranslationSplash"));
        this._splashSprite.opacity = 0;
        // Center the sprite
        this._splashSprite.anchor.x = 0.5;
        this._splashSprite.anchor.y = 0.5;
        this._splashSprite.x = Graphics.width / 2;
        this._splashSprite.y = Graphics.height / 2;
        this.addChild(this._splashSprite);
        this.startFadeIn(FADE_IN, false);
    };

    Scene_TranslationSplash.prototype.start = function() {
        Scene_Base.prototype.start.call(this);
        this._splashSprite.opacity = 255;
        SceneManager.clearStack();
    };

    Scene_TranslationSplash.prototype.update = function() {
        Scene_Base.prototype.update.call(this);

        // Skip on click / Enter / Escape
        if (Input.isTriggered("ok") || Input.isTriggered("cancel") ||
            TouchInput.isTriggered()) {
            this._fadingOut = true;
        }

        if (!this._fadingOut) {
            this._waitTimer--;
            if (this._waitTimer <= 0) {
                this._fadingOut = true;
            }
        }

        if (this._fadingOut && !this._done) {
            this._done = true;
            this.startFadeOut(FADE_OUT, false);
        }

        if (this._done && !this.isFading()) {
            _splashDone = true;
            SceneManager.goto(Scene_Title);
        }
    };

    // ── Hook: show splash before first Scene_Title ───────────

    var _SceneManager_goto = SceneManager.goto;
    SceneManager.goto = function(sceneClass) {
        if (sceneClass === Scene_Title && !_splashDone) {
            _splashDone = true;
            _SceneManager_goto.call(this, Scene_TranslationSplash);
        } else {
            _SceneManager_goto.call(this, sceneClass);
        }
    };

})();
